# AutoTest D0 契约冻结（v5.0, 硬冻结）

> 本文件是**唯一事实来源**。前端、后端、驱动、引擎、录制、报告各方向的实现必须严格对齐本文件；
> 如需变更，先向 Lead 提出"契约变更 + 理由"，由 Lead 改本文件（改完再改代码）。
> 冻结范围：错误码、DB 表结构、脚本 JSON（12 类节点）、驱动接口、运行事件、WS 三条通道、
> REST 端点清单、配置三级、录制事件与转换规则。冻结后新增字段只做**向后兼容扩展**。

## 0. 分层与目录（含文件归属）

```
backend/app/
  config.py            [Lead]  三级配置解析（default < file < db）
  db.py                [Lead]  SQLAlchemy engine/session、WAL、busy_timeout
  models.py            [Lead]  ORM 表（与 §2 一一对应）
  schemas.py           [Lead]  Pydantic（脚本/节点/目标）
  api_schemas.py       [Lead]  Pydantic（REST 请求/响应体）
  errors.py            [Lead]  ApiError + 错误码 + 异常处理器
  logs.py              [Lead]  统一 logger
  android/driver.py    [Lead]  Driver 协议 + 数据类 + FakeDriver（测试/联调）
  recorder/hooks.py    [Lead]  录制挂钩（注册表 + on_before_pointer/on_action）
  main.py              [be-core] 应用装配、路由注册、lifespan、静态托管
  deps.py              [be-core] 依赖注入（DB session、driver、service）
  services/
    device_registry.py [be-core] serial -> Driver 缓存 + 设备发现 + 占用表
    script_service.py  [be-core] 脚本 CRUD + 校验 + 导入导出
    settings_service.py[be-core] 设置读写 + 来源合并
  api/
    devices.py         [be-core] 设备/手动控制/截图/hierarchy/logcat/screenrecord
    scripts.py         [be-core] 脚本 CRUD
    settings.py        [be-core] 设置 + health + maintenance
    runs.py            [be-runner]
    recorder.py        [be-report]
    reports.py         [be-report]
    mirror.py          [be-media] 镜像 start/stop + WS
  android/
    adb.py             [be-driver] adb 封装（路径探测、exec、push/pull、设备发现）
    hierarchy.py       [be-driver] 层级解析/精简树/序列化
    selectors.py       [be-driver] Target -> 节点/坐标（含轮询等待）
    actions.py         [be-driver] 原子动作（click/swipe/key/text…）
    u2_driver.py       [be-driver] uiautomator2 实现
    screen.py          [be-driver] 截图/screenrecord
    scrcpy.py          [be-media] scrcpy server 推送/启动/视频 socket 解析
    getevent.py        [be-media] 物理录制 getevent 流解析
    touchmap.py        [be-media] 物理触摸坐标映射（旋转时间线+标定）
  mirror.py            [be-media] MirrorSession（每 serial 一个，帧广播/背压/静帧）
  runner/
    engine.py          [be-runner] 顺序执行、when/retry/on_fail/超时/三环保护
    context.py         [be-runner] 运行上下文（变量、seq、artifact、子脚本）
    executors.py       [be-runner] 12 类节点执行器
    controls.py        [be-runner] pause/resume/stop/step/run_from 状态机
  events.py            [be-runner] RunEventBus（seq 分配 + 落库 + 订阅）
  ws.py                [be-runner] /ws/runs/{id}
  recorder/
    session.py         [be-report] 录制会话（web/physical）、事件落库、转换脚本
    convert.py         [be-report] 手势分类 + 节点解析 + 低置信度标注
  report/
    builder.py         [be-report] 报告生成（zip: index.html/junit.xml/steps.json/screenshots/evidence/logs/network）
    templates/report.html [be-report]
  capture/
    logcat.py          [be-report] logcat 采集（运行期 + 手动）
    network.py         [be-report] 域名抓包（stdlib 代理，不抓正文）
frontend/src/
  main.ts / App.vue / router/index.ts / navigation.ts / layouts/AppLayout.vue / composables/useTheme.ts [fe-core]
  api/client.ts / api/<域>.ts / api/useRunEventsWs.ts / api/useRecordingEventsWs.ts [fe-core]
  types/api.ts                          [Lead]
  stores/ composables/ lib/              [fe-core 共享层；lib/h264|webcodecs|mirrorWs 归 fe-mirror]
  features/console|devices|scripts|runs|recorder|settings/** [fe-core]
  features/mirror/**                    [fe-mirror] 镜像画布/WebCodecs/输入映射/录制工具条
  styles/index.css                      [fe-core] 设计 token + shell + Element Plus 覆写
frontend-react/  v0.1.3 React 冻结备份（不参与构建，仅回滚/对照）
```

**写作用域纪律**：只改自己名下的文件。需要改别人文件 → 发消息给 Lead。需要加依赖 → 发消息给 Lead（禁止自己 `uv add` / `npm i`）。
共享内核（config/db/models/schemas/api_schemas/errors/logs/driver/hooks/types）由 Lead 维护，**不要重写**；发现契约缺陷 → 报告 Lead。

**测试要求**：每个后端队员在自己范围内写 pytest（`backend/tests/test_<域>_<模块>.py`，命名不冲突），
全部用 FakeDriver / 假 socket / tmp_path，**不依赖真机**；`uv run pytest` 全绿才算完成。
前端 `npm run typecheck` 通过；页面用 mock 数据可渲染。

## 1. 错误码与响应信封

成功：直接返回资源 JSON（列表返回 `{"items": [...], "total": n}`）。
失败（所有 4xx/5xx，含 422）：
```json
{"error": {"code": "NOT_FOUND", "message": "script not found", "details": {}}}
```
| code | HTTP | 场景 |
|---|---|---|
| INVALID_ARGUMENT | 400 | 参数非法（如 point 缺坐标、同时给 x 和 nx） |
| VALIDATION_ERROR | 422 | Pydantic/脚本校验失败，details.errors 为逐项错误 |
| UNAUTHORIZED | 401 | 配置了 token 但未带 |
| NOT_FOUND | 404 | 资源不存在 |
| CONFLICT | 409 | 运行中重复启动、录制中被抢占 |
| DEVICE_UNAVAILABLE | 503 | adb 未就绪 / 设备离线 |
| DEVICE_BUSY | 409 | 设备被其他运行/录制占用 |
| SCRCPY_FAILED | 502 | 镜像启动或协议失败 |
| UNSUPPORTED | 400 | 当前平台/浏览器/设备不支持 |
| INTERNAL | 500 | 未预期异常（日志带上下文） |

## 2. DB 表（SQLite，WAL，短事务，busy_timeout=5000）

时间字段统一 ISO8601 UTC 字符串，写库用 `models.now_iso()`。
表结构以 `backend/app/models.py` 为准——runs/step_results/run_events/artifacts/recordings/
recording_events/reports，索引见 models.py。

**v0.2.8 起脚本与配置不存 DB：**
- 脚本 = `<scripts_dir>/<id>.json`（默认 `~/.autotest/scripts`，`runtime.scripts_dir` / `AUTOTEST_SCRIPTS_DIR` 可改），
  文件内容 `{id,name,notes,source,origin_recording_id,schema_version,created_at,updated_at,data}`；
- 配置 = `~/.autotest/config.json`（见 §11）；
- `scripts` / `settings` 两张表只作为旧库迁移来源：启动时 `migrate_db_scripts_to_files()` /
  `migrate_db_settings_to_file()` 写入文件后清空表；文件已存在时不覆盖。
状态枚举：
- run.status：`queued|running|paused|stopping|succeeded|failed|aborted|stopped|interrupted`
- step_results.status：`passed|failed|skipped|error`
- recording.status：`recording|paused|stopped`
- artifact.kind：`screenshot|video|logcat|hierarchy|report|screenrecord|network`
- 失败节点会额外写两类证据 artifact（`step_result_id` 关联）：
  - `kind=logcat`：节点失败时的活跃 logcat 采集尾部（未开启采集则跳过，不额外等 adb dump）；
  - `kind=network`：节点开始时间窗内的域名记录（需网络抓包处于活跃状态）。
  这是失败现场快照，不做全量节点归类和逐请求归因。

### 2.1 v0.2 数据库与来源字段（不兼容 v0.1.x）

- **旧数据库不支持**：v0.2 使用新库，不做 ALTER TABLE / 回填 / 旧数据迁移。`init_db()` 建表后执行
  schema guard：若 `scripts` 表缺少 `origin_recording_id` 列，抛 `IncompatibleDatabaseError`（RuntimeError
  子类），提示用户**先备份**，再移走/删除 `~/.autotest/data`（或自定义数据目录）下的旧库；绝不自动修改/删除数据。
- `scripts.source` 四值：`manual | mirror | physical | imported`（旧 `recorded` 不再支持）。
  - `manual`：手写/新建；`imported`：导入；`mirror`：web 镜像录制转换；`physical`：物理触摸录制转换。
- `scripts.origin_recording_id`：`String(64)`、nullable、**软关联**（无 FK）。录制删除后仍保留，便于溯源；
  v0.2 不新增 `source_detail`。
- `Recording.mode=web` → 转换脚本 `source=mirror`；`mode=physical` → `source=physical`。
- 删除脚本时清空指向它的 `recordings.script_id`（录制本身与事件保留）；删除录制时保留脚本的
  `source` / `origin_recording_id`。

## 3. 脚本 JSON（schema_version=1，唯一数据文件格式）

结构校验用 `app.schemas`（Lead 已写，12 类节点 params 见其模型定义）；语义校验（id 唯一、
goto 目标、sub_script 存在性 warning）用 `schemas.validate_script()`。

- `variables`：`{name: str}`，脚本级默认值；运行时可被请求 `variables` 覆盖（只影响这一次运行）。
  生效优先级：`sub_script 父上下文` < 脚本 `variables` < 本次运行请求 `variables`；`sub_script` 节点的
  `variables` 再叠加给子脚本子树。
  插值（`{{name}}`，变量名 `[A-Za-z0-9_.-]+`，单遍替换不递归）作用范围：
  - 所有 target 的 `value`（click / long_click / swipe / drag / wait / assert / when 条件）；
  - `input.text`、`input.target.value`；`app.package|activity`；`shell.command`；`assert.value`；
    `sub_script.script_id|variables 的值`；`screenshot.name`；`when` 里 app 条件的 package/activity。
  - **不插值**：target 的 `point.x/y`、`index`、`by`、`key/keycode`、节点 name/description/tags、
    `on_fail.target_id`、wait 的时长等数字/枚举字段。
  未知变量（未声明且未覆盖）运行时原样保留并 emit 一条 `log` warn。
  前端编辑器对上述可插值字段提供「{{ }}」变量选择器（插入到光标处），并在节点用到未声明变量时提示。
- 存储位置：脚本 JSON（含 steps）是**文件**，`<scripts_dir>/<id>.json`；DB 只存运行记录和
  `runs.script_snapshot`（运行时的脚本快照，改脚本不影响历史记录）。
- 节点公共字段：id/name/type/description/enabled/when/timeout_ms/delay_before_ms/delay_after_ms/
  retry/on_fail/screenshot_on_fail/rerun_safety/tags/params。
- 节点停顿 `delay_before_ms` / `delay_after_ms`：动作之外的节奏停顿，不计入节点超时。
  **0 = 跟随脚本默认**（`settings.default_delay_before_ms` / `settings.default_delay_after_ms`，默认 0 即不停顿），
  **非 0 = 该节点自定义**（覆盖默认，不叠加）。录制转换把真实操作间隔还原进节点 `delay_before_ms`
  （这些值 >0 即自定义），生成脚本的 `settings.default_delay_*` 固定写 0，所以录制回放节奏不受默认停顿影响。
- 失败处理顺序（与 UI 文案一致）：
  1. `when` 条件：`require` 不满足 → 节点直接 failed，**不重试**；`optional` 不满足 → skipped。
  2. 动作异常：可重试且已有重试次数未用完 → 等待 `interval_ms` 后重试同一动作；`retry.count` 是
     **额外重试次数**（1 = 最多执行 2 次）。断言失败、when 不满足、运行被停止、超时后线程未收尾等
     属于不可重试。
  3. 重试用尽 / 不可重试 → 节点 failed。
  4. `on_fail`（节点优先，其次脚本 `settings.on_fail`）：`abort` 中止整个运行 / `continue` 继续下一
     节点 / `goto` 跳到目标节点（重入会重置目标及之后节点的重试计数）。
  5. `screenshot_on_fail`：节点开关 **且** 脚本 `settings.screenshot_on_fail` 都为真才保存截图。
  节点 `retry=null` 表示继承脚本默认；节点 `retry={count:0}` 表示显式不重试；脚本
  `settings.retry=null` 表示默认不重试。旧字段 `defaults.retry` 在读取/落盘时折叠进
  `settings.retry`（`ScriptData` validator + `script_service`），不再作为第二个来源。
  `rerun_safety` 只用于手动「从该节点重跑」的安全判断，不参与运行时失败处理。
- 新建脚本初值：`POST /api/scripts` 建空脚本时，`create_script` 会把 `engine.default_delay_before_ms` /
  `engine.default_delay_after_ms` / `engine.default_on_fail`（设置页 → 执行 → 运行节奏）写进脚本
  `settings`；传入的 data 已显式带这些字段时（导入/模板/复制）不覆盖。这是**只写一次初值**，
  运行时不做第三层兜底，所以全局值改动不会影响已有脚本与录制脚本。
- 脚本 `settings.default_rerun_safety`（safe/stateful/destructive）：新建节点的 `rerun_safety` 初值，
  只影响之后新增的节点；已有节点保持自己的值，不参与运行时继承。
- Target：`{"by": "...", "value": "...", "index": 0}` 或 `{"by": "point", "x": 540, "y": 1200}`。
- `source` / `origin_recording_id` 是 DB 资源元数据（见 §2.1），不属于脚本 JSON。
- when：`null` 或一个新的条件树对象；**不接受** v0.1 的 `when.target/state` 结构。
  ```json
  {"mode": "require|optional", "timeout_ms": 0, "poll_interval_ms": 350,
   "condition": {"type": "target", "target": {"by": "text", "value": "登录"}, "state": "visible"}}
  ```
  `timeout_ms` ≥ 0（0=只求值一次）；`poll_interval_ms` 50..5000（默认 350）。
  `require` 不满足 → 节点失败 `WHEN_NOT_MET` 并走 on_fail；`optional` 不满足 → `skipped`（reason=`when_not_met`）。
  condition 是带 `type` 判别字段的递归树（v0.2 实现以下 5 类，变量/设备条件留给后续版本扩展）：
  - `target`：`{type, target, state: visible|gone}`；`point` 不能作为可见性条件（校验报 error）。
  - `app`：`{type, package, activity?, state: foreground|background}`；activity 支持全名或 `.MainActivity` 简写。
  - `time`：`{type, start: "HH:MM", end: "HH:MM", days: [1..7]}`；宿主本地时区，1=周一、7=周日；
    `start > end` 表示跨午夜，`days` 指窗口开始那一天（如周六 01:00 仍属于「周五 22:00-06:00, days=[5]」）；
    `start == end` 表示全天。
  - `group`：`{type, op: and|or, items: [...]}`，至少 1 项、至多 50 项。
  - `not`：`{type: "not", item: <condition>}`，单项取反。
  - `group`/`not` 嵌套 ≤4 层（叶子不计层）。
- 断言失败走 on_fail 但**不重试动作**；sub_script 嵌套 ≤5；click 类 target 解析失败→可重试。

## 4. 驱动接口（`app/android/driver.py`，Lead 已写）

同步接口。所有注入动作的录制钩子由 **API 层**调用，驱动层不感知录制。
- `HierarchyNode`：精简树（class_name/text/resource_id/content_desc/package/bounds/clickable/enabled/visible/children）。
- `get_driver(serial) -> Driver` 由 `services/device_registry.py` 缓存；`serial=None/"auto"` 取唯一在线设备。
- `FakeDriver`：内存假设备（假层级树 + 调用记录 + 可编程 shell），供引擎/前端联调与单测，不依赖真机。
- u2 实现（be-driver 的 u2_driver.py）必须满足同名语义；`clear_text` 用 adb 批量 keyevent（不等界面空闲）。
- **文本输入同样 adb first**：ASCII 走 `adb shell input text`（空格转 `%s` + shell 转义）；非 ASCII 用无障碍
  ACTION_SET_TEXT（u2 `set_text`）。u2 `send_keys` 是「写剪贴板 + 注入 Ctrl+V」，部分机型/游戏输入框上粘贴
  不生效、Ctrl+V 被当普通按键只输入一个 "v"（实测），不能作为唯一路径。
- **注入一律 adb first**（click/swipe/long_press/drag/key）：u2 注入 RPC 在 Unity/常驻动画界面上会同步等
  「界面空闲」约 10s，点击已生效但节点会先被 8s 节点超时判失败；`adb shell input` 不等空闲（实测 ~0.1s），
  失败才回落 u2。
- `app_clear` 清完数据会自动 `pm grant` 该应用声明的运行时权限（通知/存储/相机…），避免下次启动弹系统
  授权框挡住脚本；另有 `app_grant` 驱动方法/`app grant` 节点动作单独授权（不改数据）。

## 5. 运行事件（run_events.type）

| type | payload 关键字段 |
|---|---|
| run_started | {script_id, script_name, device_serial, total_steps} |
| run_state | {status, step_index?} |
| when_started | {step_id, mode, timeout_ms, poll_interval_ms, condition_summary} |
| when_result | {step_id, mode, met, waited_ms, condition_summary} |
| step_started | {step_id, name, type, index, attempt} |
| step_finished | {step_id, status, duration_ms, attempts, error?, output?, when?} |
| step_skipped | {step_id, reason: when_not_met\|disabled\|after_fail, when?} |
| step_retry | {step_id, attempt, error} |
| assert_result | {step_id, kind, passed, detail} |
| log | {level, message} |
| screenshot | {artifact_id, step_id, name} |
| run_paused | {reason: user, at_step_id} |
| run_resumed | {resume_kind: hot, warnings: []} |
| manual_intervention | {kind, payload} |
| run_finished | {status, duration_ms, passed_steps, failed_steps, skipped_steps} |
| error | {code, message, step_id?} |
seq：同一 run 单调递增，由 `SELECT MAX(seq)+1` 在短事务内分配，`(run_id, seq)` 唯一约束兜底（冲突重试≤3）。
WS 订阅者从内存 pubsub 收实时事件；断线用 `?since_seq=n` 从 DB 补。

**when 可观测性（v0.2）**
- 事件顺序：成功 → `when_started → when_result → step_started → step_finished`；
  optional 不满足 → `when_started → when_result → step_skipped`；
  require 不满足 → `when_started → when_result → step_finished(failed, error.code=WHEN_NOT_MET)`。
- `when` 对象：`{met, waited_ms, condition_summary}`。条件节点的 `step_results.output.when` 必带该对象，
  `step_finished` / `step_skipped` payload 也带可选 `when`；无条件节点输出与 payload 不出现 `when`。
- `WHEN_NOT_MET` 的 `error.details.when` 同样带该对象。
- `duration_ms` 仍只统计动作（when 等待不算）；等待时长只记在 `when.waited_ms`（活跃时长口径，paused 不计）。

## 6. WebSocket（FastAPI 原生 WebSocket，路径挂在根，不走 /api）

### 6.1 `/ws/runs/{run_id}?since_seq=0`
连接后先发 `{"kind":"state","run_id":1,"status":"running","last_seq":12}`，然后补发 since_seq 之后的历史，再实时推送。
事件帧：`{"kind":"event","seq":13,"type":"step_started","ts":"ISO","payload":{}}`。
client→server：`{"kind":"ping"}`→`{"kind":"pong"}`；`{"kind":"ack","last_seq":13}`（仅记 meta）。

### 6.2 `/ws/mirror/{serial}?token=`
1) 首条 text 为 hello：`{"kind":"hello","serial","session_id","width","height","rotation","codec":"avc1.x","sps_b64","max_size","scale_pct","configured_max_size","max_fps","video_bit_rate"}`。
   ⚠️ 其中 `max_size` 是**已算过缩放的实际上限**（设备长边 × `scale_pct`，再受用户上限约束，例：2670×70%→1868），
   `configured_max_size` 才是设置页的上限值；前端判断“设置是否已变更”必须比较 `scale_pct`/`configured_max_size`，
   直接把 `max_size` 和 `scrcpy.max_size` 比较会产生常驻假提示（`config` 控制消息同此语义）。
2) 视频帧为 binary：`[u64 BE pts_us][u32 BE payload_len][Annex B payload]`（12 字节头）。
3) 控制 text：server→`{"kind":"config",...}`（参数变化/旋转）、`{"kind":"flush","reason":"keyframe_drop"}`、
   `{"kind":"error","code","message"}`、`{"kind":"stats","fps","dropped"}`；client→`{"kind":"request_keyframe"}`、`{"kind":"ping"}`、
   `{"kind":"touch","action":"down|move|up","nx","ny"}`。
   `request_keyframe` 服务端按 0.8s 节流（每次 RESET_VIDEO 都是编码器重启，100~300ms 不出帧）；
   新订阅者需要的 IDR 由服务端在 subscribe 时强制请求，客户端 open/hello 不必再要。
   前端收到 `flush` 只把状态置为“等下一个 IDR”，不重建 VideoDecoder（IDR 会刷新参考帧）。
4) 背压：消费者慢→丢帧到下一 IDR 边界，补发整 GOP，先发 flush。
5) 坐标：镜像页拖动首选同一条 WS 的 `touch` 消息（严格有序、无 HTTP 开销）；REST `/touch|tap|swipe`（§7.1）为同语义兜底。
   坐标统一为归一化 nx,ny∈[0,1]（相对视频帧），后端按镜像几何反算设备像素。归一化是前端唯一坐标表达。
   Chrome 下拖动样本走 `pointerrawupdate`（不对齐 rAF，比 pointermove 早最多一帧），pointermove 只补未发过的样本。
   **每段手势必须有 up 收尾**：画布捕获失效时由 window 捕获阶段的 `pointerup/pointercancel` 兜底；
   鼠标已松开却仍收到 `pointermove/pointerrawupdate`（`buttons=0`）视为 UP 丢失，用当前位置补 up；
   窗口失焦/页面隐藏/组件卸载用最后位置补 up；WS 发不出 up 时静默回落一次 REST `/touch`。
   前端在 `up` 之前被拒绝而残留的手势，由下一次 `down` 先补 up（后端 owner 语义不变）。
6) 无 WebCodecs：前端显示引导页，后端**不**降级截图轮询。

### 6.3 `/ws/recordings/{recording_id}?since_seq=0`
`{"kind":"event","seq","type","payload","confidence","ts"}` / `{"kind":"state","status","event_count"}`；ping/pong 同上。

## 7. REST 端点（/api 前缀；JSON；请求体见 app/api_schemas.py）

### 7.1 设备与手动控制（be-core）
GET /api/devices；POST /api/devices/connect {address}；POST /api/devices/{serial}/disconnect；
GET /api/devices/{serial}/info|hierarchy?raw=|screenshot(png)|apps?keyword=&type=(all|system|third_party，`third` 为兼容别名)|logcat?lines=200；
POST /api/devices/{serial}/logcat/start|stop；GET /api/devices/{serial}/logcat/status
（start 可带 run_id/max_lines/package；package 按运行时 PID 过滤）。
`capture.logcat_auto_attach` 是采集总开关：关闭时 start 返回 UNSUPPORTED，脚本 settings.logcat 也无效（编辑器置灰）；
stop/status 不受开关限制（避免关开关后残留会话停不掉），status 带 `enabled`。运行期采集需
总开关 + 脚本 settings.logcat 同时为 true，artifact 关联 run。
脚本可设 `settings.logcat_scope`：`device`（默认，整机日志）或 `app`（只保留脚本涉及应用）；
`settings.logcat_package` 显式指定包名，留空时从 `app` 节点的 `params.package` 推断（多个包合并监听）。
app 范围按运行时 PID 过滤：开始采集解析一次、后台每 1.5s 刷新（App 重启后 PID 变化自动跟上），
日志行按 PID 保留、堆栈续行跟随上一条；手动 start 的 `package` 参数同样按 PID 过滤。
  hierarchy 响应：`{"node": HierarchyNode, "raw": str|null, "elapsed_ms": int, "node_count": int}`
  （raw=true 时**只 dump 一次**：用原始 XML 解析成树；取不到 raw 时为 null 并带 `"raw_unavailable"`。
  `elapsed_ms`/`node_count` 供前端显示"刷新慢在哪"；后端对 >800ms 的 dump、>1500ms 的截图打 WARNING）；
POST /api/devices/{serial}/tap|long_press|swipe|key|text|app|shell；
POST /api/devices/{serial}/screenrecord/start {name,bit_rate,time_limit_s} / stop（仅 API：控制台「设备录屏」入口已移除，视频录制走浏览器端）；
GET /api/devices/{serial}/screenrecord/status → {active,pending,name,time_limit_s,elapsed_s,remaining_s,started_at,last:{name,size,url}}；
  last 为最近一次录屏产物直链（/artifacts/screenrecord/…，支持 Range 播放/拖动）；
POST /api/devices/{serial}/network/start|stop；GET /api/devices/{serial}/network/status
（start 在总开关关闭时→UNSUPPORTED；stop/status 不受限，status 带 `enabled`）。
域名模式：本机 stdlib 代理 + 设备 http_proxy + adb reverse，
只记录 host/port/scheme，不安装 CA、不抓 Header/正文；同一时间只允许一台设备抓包；
停止时恢复设备原 http_proxy。
`capture.network.enabled` 是总开关；脚本 `settings.network_capture`（bool，默认 false）决定本次运行是否抓：
两者同时为 true 才在运行中自动启停并关联 run_id；手动抓包还需总开关打开，且不会被运行结束误停。
旧配置键 `capture.network.mode`（off|manual|run）在读取时映射为 enabled（off=false，其余=true）。
POST /api/devices/{serial}/mirror/start|stop；GET /api/devices/{serial}/mirror/status；
POST /api/devices/{serial}/mirror/diag：浏览器每 5s 上报一条诊断采样（渲染 fps/绘制耗时/解码队列/丢帧），
落盘 `<data_dir>/diag/mirror-YYYYMMDD.jsonl`（`source=client`；后端每 10s 的 perf 打点为 `source=server`），
由 `scripts/analyze_mirror_diag.py` 分析；写盘失败只告警，不影响镜像。
> 录制挂钩：serial 有活跃录制时注入动作必须经 `app.recorder.hooks` 留痕（on_before_pointer→注入→on_action）。

### 7.2 脚本（be-core）
GET /api/scripts?query=&limit=&offset=；POST /api/scripts；GET/PUT/DELETE /api/scripts/{id}；
POST /api/scripts/{id}/duplicate；POST /api/scripts/validate {data}；
GET /api/scripts/{id}/export（json 附件）；POST /api/scripts/import（multipart 或 {data}）；
POST /api/scripts/{id}/run {serial,...}（快捷=POST /api/runs）。
- 脚本资源带 `source`（四值，见 §2.1）与 `origin_recording_id`（nullable）。
- 脚本资源新增 **additive** 字段 `last_run`：当前脚本最新一条 Run 的摘要
  `{id,status,started_at,ended_at,created_at,device_serial,error,summary}` 或 `null`；
  list/get/update 返回（新建/复制/导入为 `null`）。列表按当前页 script_ids 用一次窗口查询
  取各脚本最新 run，禁止 N+1；不新增表/迁移。
- `DELETE /api/scripts/{id}` 同时清空 `recordings.script_id`（软关联），不删除录制/事件。
- `POST /api/scripts/bulk-delete {ids: [str]}（≤200）` → `{deleted, failed:[{id,code,message}]}`；
  ids 去重，空列表 400 INVALID_ARGUMENT；单条失败不影响其他条。不存在 → failed NOT_FOUND；
  删除成功的脚本同样清空 `recordings.script_id`。

### 7.3 运行（be-runner）
POST /api/runs {script_id,serial?,variables?,debug?}（占用/录制中→409）；
GET /api/runs?status=&script_id=&limit=&offset=；GET /api/runs/{id}（含 steps + artifacts：
`[{id,kind,name,path,url,size,created_at}]`，url 为 `/artifacts/<相对 artifacts_dir 路径>`，无直链为 null）；
运行资源 additive 字段：`variables`（本次请求的覆盖，只含与脚本默认不同的项）与
`effective_variables`（脚本快照 variables + 覆盖，即本次运行实际生效值），用于回查运行参数；
DELETE /api/runs/{id}；GET /api/runs/{id}/events?since_seq=&limit=；
POST /api/runs/{id}/pause；**POST** /api/runs/{id}/resume-check（返回 §7.3 预检字段）；
POST /api/runs/{id}/resume {ack,resume_kind:"hot"}；POST /api/runs/{id}/stop；
POST /api/runs/{id}/debug {action:"step"|"continue"|"run_from", step_id?}。
暂停期间的手动控制放行，并必须 emit `manual_intervention` 运行事件（见 §5）。
- `POST /api/runs/bulk-delete {ids: [int]}（≤200）` → `{deleted, failed:[{id,code,message}]}`；
  ids 去重，空列表 400 INVALID_ARGUMENT；只允许 terminal 状态（succeeded/failed/aborted/stopped/
  interrupted），进行中/暂停中等非终态 → failed CONFLICT，**不自动 stop**；终态删除复用单删清理逻辑
  （step/events/artifacts/产物目录）。不存在 → failed NOT_FOUND；单条失败不影响其他条。

### 7.4 录制（be-report）
POST /api/recordings {name?,serial,mode}；GET /api/recordings?status=&mode=&limit=&offset=（v0.2.5 起支持 mode/offset）；
GET /api/recordings/{id}；POST /api/recordings/{id}/pause|resume|stop；
GET /api/recordings/{id}/events?since_seq=&limit=；DELETE /api/recordings/{id}/events/{seq}；
POST /api/recordings/{id}/to_script {name?,include_kinds?,exclude_seqs?}
→ `{id, script_id, name, data, source, origin_recording_id, warnings:[{seq,reason}]}`；DELETE /api/recordings/{id}。
- 录制资源（创建/查询/列表/暂停恢复停止）响应新增 `script_name`：由 `script_id` 软关联查询；
  列表按当前页 `script_ids` 批量查询一次，禁止 N+1。未关联/脚本已删除时为 `null`。
- `to_script` 按 `Recording.mode` 写 `source`：`web → mirror`、`physical → physical`；写
  `origin_recording_id=recording_id`。转换出的所有节点（含自动聚焦节点）`when` 恒为 `null`。
- `DELETE /api/recordings/{id}` 只删录制与事件，不删除/改动关联脚本的 `source` / `origin_recording_id`。
- `POST /api/recordings/bulk-delete {ids: [str]}（≤200）` → `{deleted, failed:[{id,code,message}]}`；
  ids 去重，空列表 400 INVALID_ARGUMENT；只允许 stopped，recording/paused → failed CONFLICT，
  **不自动 stop**；不存在 → failed NOT_FOUND；删除成功时删除事件与录制，单条失败不影响其他条。
- `POST /api/recordings/{id}/events/bulk-delete {seqs: [int]}（≤500）` → 同统一响应（failed 的 id 为 seq）；
  seqs 去重，空列表 400 INVALID_ARGUMENT；事件不存在 → failed NOT_FOUND；删除后重算
  `recording.event_count`；单条失败不影响其他条。

### 7.5 报告与维护（be-report）
POST /api/runs/{id}/report {include_video?}；GET /api/reports?run_id=；
GET /api/reports/{id}/download（zip，支持 Range）；DELETE /api/reports/{id}；
GET /api/maintenance/stats；POST /api/maintenance/cleanup {retention_days?,dry_run?}。
报告 zip：`index.html` / `junit.xml` / `steps.json` / `screenshots/` /（可选）`videos/`；
节点证据按 `artifact.step_result_id` 关联：失败节点的 logcat 与域名清单进 `evidence/`，并在
`index.html` 节点时间线的「失败现场」、`steps.json[].evidence`、junit `<system-out>` 中呈现；
运行级 logcat / 域名采集分别放 `logs/`、`network/`。
时间线：运行级 logcat（`adb logcat -v time`，设备本地时间）、域名 JSONL 的 `ts` 与
`run_events`（操作/状态事件）按 `StepResult.started_at/ended_at` 归入节点；`index.html` 提供
「节点时间线」和「合并时间线」两种视图，`steps.json[].timeline` 带节点窗口内的
logcat/network/events 条目；落在所有窗口外的记录进入「节点外」。设备时区需与后端一致，
否则 logcat 时间线整体偏移（原始文件仍在 zip 内）。

### 7.6 设置（be-core）
GET /api/settings → {effective, sources, schema, config_file}
  （schema 每项含 additive `default` / `default_display`（空值默认的可读展示，如数据目录绝对路径） /
  `layer`(bootstrap|runtime) / `writable` / `env`）；
PUT /api/settings {values}（写 config.json；null=从文件移除该项；bootstrap 项 → 400 INVALID_ARGUMENT）；
DELETE /api/settings/{key}（从文件移除；bootstrap 项 → 400）；
POST /api/settings/reset（移除文件里所有设置页管理的项，启动项不动）；
POST /api/settings/reload（重新读取 config.json；返回 restart_required 列出需重启的启动项）；
GET /api/settings/scrcpy/options（白名单+校验，渲染表单用）；
GET /api/health → {status,version,db,adb:{path,version,devices},data_dir,scripts_dir,config_file}。

v0.2.5 起 `SETTINGS_SCHEMA` 补充两个 `DEFAULTS` 已有但未展示的 key：
`adb.connect_timeout_s`（int，1..120，工具链 → ADB）、`scrcpy.jar`（string，工具链 → scrcpy-server，留空用 vendor 内置 jar）。
v0.2.6 另新增 `scrcpy.scale`（int，50..100，按设备自然长边缩放百分比），`scrcpy.max_size` 改为缩放后的长边上限
（默认 4096，不再放大）；`scrcpy.max_fps` 范围改为 0..240、**默认 0（不限，跟随设备刷新率，与 scrcpy 一致）**；
`MirrorStartRequest.max_fps` 同步为 0..240。
不改 settings 表结构、不做 migration；主题/语言只存前端 localStorage。

## 8. 录制挂钩（`app/recorder/hooks.py`，Lead 已写）

```python
pre = hooks.on_before_pointer(serial)   # click 类注入前，同步 dump_hierarchy（无录制→None）
driver.click(x, y)
hooks.on_action(serial, "tap", {"x": x, "y": y, "duration_ms": 0}, pre)
# 滑动：环缓冲 + 事后补抓（session 内部，≤2 dumps/事件）
hooks.on_action(serial, "swipe", {...})
```
kind ∈ tap|long_press|swipe|drag|input|key|app。
RecorderSession 需实现：`before_pointer() -> dict|None`、`handle(kind, payload, pre)`、`note_manual(kind, payload)`。

v0.2.5 手势回放补充：
- 录制事件 `swipe/drag` 可带 `path:[{x,y,dt_ms}]`（物理录制与镜像录制都采集；最多 240 点）。
- 手势分类同时看「起点→终点直线位移」与「轨迹离起点的最大距离」：画圆/画圈即使回到起点，
  只要轨迹范围超过 10px，就按 swipe/drag 保留 path，不再误判成点击/长按。
- 回放优先级：当前设备有活跃镜像会话 → 通过 scrcpy control 通道按 `dt_ms` 逐点注入 DOWN/MOVE/UP，
  保留轨迹形状、变速和末端停顿；没有镜像会话 → 退化为两点 u2 swipe。
- 不再使用 u2 `swipePoints` 回放录制轨迹（部分 Unity/游戏设备上会长时间不返回，卡到节点超时）；
  无轨迹的手动 drag 节点仍走驱动原生 drag。
- 节点超时自动覆盖 `duration_ms + 2s`，避免长按/长滑被默认 8s 超时截断。
物理录制：`app.android.getevent.stream_events(serial, on_touch, on_key, stop_event)` 产出
`TouchSample{t,x,y,down,tracking_id,pressure}` / `KeyEvent{t,keycode,down}`，PhysicalRecorder 消费并转换。

## 9. 时间线与三环保护（be-runner 必做）

- 轮询型等待统一 350ms（config engine.poll_interval_ms）；所有等待可被 pause/stop 打断（≥每 350ms 检查）；
- 节点 timeout_ms（默认 settings.default_timeout_ms，上限 600s）→ StepTimeout；
- 节点停顿 `delay_before_ms` / `delay_after_ms` 在动作前/后执行，实际值 = 节点值（>0）否则脚本默认
  `settings.default_delay_*_ms`；停顿同样可被 pause/stop 打断（`sleep_interruptible`），且录制脚本的默认停顿为 0；
- 节点超时三条规则：① 显式 `timeout_ms` 永远是硬上限；② 未显式设置时，`wait` 节点按
  「等待时长（sleep 的 ms / until 的 timeout_ms）+ 2s」自动抬升，避免继承的 8s 默认值静默截断；
  ③ `sub_script` 节点未显式设置时不再用脚本默认 8s 切整段子脚本，最多到 step_max_timeout_ms/总预算
  （子脚本内部各节点仍有自己的节点超时）。撞到引擎上限时错误信息会写明「等待 X ms 超过节点超时上限
  Y ms，已按上限截断」；
- 运行总时长 engine.max_total_ms（默认 30min，**活跃时间**口径，paused 不计）；
- 步骤总预算 engine.max_steps（默认 2000，含 retry/goto 重跳）；
- goto 重入重置目标节点及其后的 retry 计数；重跑当前节点清零；
- stop 优先级最高：正在执行的动作最多等 5s 后强切（节点 failed，error.code=STOPPED）；
- 节点级取消令牌：节点超时/stop 强切时置位，执行线程在定位轮询与每次注入前检查；
  scrcpy 轨迹回放中止时补一个 UP 并抛错，**不**回落到 u2 两点 swipe；
- 节点超时后若执行线程在 200ms 收尾窗口内仍未退出（卡在设备调用里），本轮**不重试**
  （错误码仍为 STEP_TIMEOUT），避免同一节点产生两个并发执行；
- 镜像流式触摸按 WS 订阅者 owner 隔离（一个 serial 同时只有一个 owner 的手势）；
  WS 断开时为该 owner 补 UP，超过 30s 的陈旧手势在下一次 down 前释放；
- pause/resume/step/run_from 由 controls 状态机管，run 从线程池执行（asyncio.to_thread 包同步引擎）。

## 10. scrcpy 镜像（be-media 必做）

- 固定 scrcpy-server **v5.0.1**（官方资产名 `scrcpy-server-v5.0.1`，**无 .jar 后缀**，落地为
  `backend/vendor/scrcpy-server-v5.0.1.jar`，官方 SHA256 `764eb6f79811d5211fe9df341120882ba9994c7a61b897d7bf3fb662e53bc536`，
  锁文件 `backend/vendor/scrcpy.lock.json` 已就位）；
- 启动参数（v5.0.1 实测合法集合）：`log_level max_size max_fps video_bit_rate crop display_id audio video
  control tunnel_forward send_device_meta send_frame_meta send_dummy_byte send_stream_meta clipboard_autosync power_on cleanup scid`；
  **没有 `socket=`**；socket 名由 `scid` 派生：`localabstract:scrcpy_<scid 8位小写hex>`；
  固定 `audio=false video=true control=true tunnel_forward=true send_device_meta=true send_frame_meta=true
  send_dummy_byte=true send_stream_meta=true`；jar 每次 start 前校验/必要时重推，清理时**不删设备端 jar**；
- 字节流（v5.0.1 `device/Streamer.java` 实证）：
  `[1B dummy=0x00][64B device name][4B codec id "h264"][12B session meta: flags u32|w u32|h u32]`，
  之后每包 `[12B: pts_and_flags u64 BE | size u32 BE][size B Annex B payload]`；
  flags：bit61=KEY_FRAME，bit62=CONFIG（CONFIG 包=SPS/PPS，必须消费并拼到下一个视频帧前）；
  pts = pts_and_flags & ~(1<<61) & ~(1<<62)；session meta 变化（旋转/改尺寸）→ 重发 config；
  连接顺序 video → control（只连 video 时 server 不出帧）；
- 后端→前端 WS 帧保持 `[u64 pts][u32 len][Annex B payload]` 不变（前端按此实现）；
- 分辨率：`scrcpy.scale`（50..100%）按设备自然长边缩放，再由 `scrcpy.max_size` 封顶；启动时探测
  `wm size`，探测失败回退到绝对上限；不放大。三档画质：轻量 70%、标准 85%、高清 100%（100% 为原生；4096 为 scrcpy 硬上限）；
- 帧率：`scrcpy.max_fps` 默认 **0** = 不限（跟随设备刷新率），透传 `max_fps=0`。
  已对 scrcpy master 源码核实默认值：`server/.../Options.java` 里 `maxFps`（float，未赋值即 0）、`maxSize`（0 = 不缩放）、
  `videoBitRate = 8000000`（8 Mbps）；`doc/video.md` 亦写明“默认按设备分辨率镜像”“默认码率 8 Mbps”。
  30fps 会多一帧采样等待且滚动不顺，是与 scrcpy 观感差距最大的单一因素；
  帧率是与画质正交的独立设置：限帧只在电脑解码跟不上时才需要（解码队列积压会主动丢帧，表现为一顿一顿），
  官方对性能问题的建议是降分辨率/码率而不是限帧。预设只描述画质（缩放+上限+码率），不含帧率：
  轻量 70%/6M、标准 85%/10M、高清 100%/16M；调帧率不会让预设变成“自定义”。
  `from_settings()` 对该 key 不做 `or` 兜底（0 是合法值）；
- 关键帧：`split_nals` 用 `bytes.find` 定位起始码（逐字节版在 i7-4578U 上扫一个 291KB IDR 要 37ms 并独占 GIL，
  卡住 asyncio 循环上的触摸/WS 发送）；前端热路径 `inspectAnnexB` 只看前导 NAL，P 帧不再全量切分；
- 旋转：视频尺寸变化→新 SPS→重发 config；stream→device 逆映射由 MirrorSession 维护（含 max_size/crop/rotation）；
- 崩溃/断线自动重启（≤3 次/分钟），WS 发 error 保持连接；
- 启动自检：socket 数、首帧 ≤8s、codec 字符串从 SPS 解析；
- 孤儿清理：只清理本进程历史 session 的 localabstract forward，不动别人的。

## 11. 配置分层（be-core）

配置**只存文件**，三层，从低到高：**代码默认 < 配置文件 < 环境变量**。数据库只存运行数据
（runs/step_results/run_events/artifacts/reports 等），不再有 settings 表这一层。

- 配置文件：`$AUTOTEST_CONFIG` 或 `~/.autotest/config.json`；嵌套 JSON，按点号读写
  （`{"engine":{"default_timeout_ms":5000}}` ↔ `engine.default_timeout_ms`）。
- 设置页保存 = **原子写配置文件 + 立即 `reload_file_layer()`**（临时文件 + `os.replace`），
  所以保存后即时生效；其余 key 的生效时机见 §9/§12.1（engine 下一次运行、scrcpy 重启镜像）。
- 手改配置文件后：`POST /api/settings/reload` 重载，或重启；非法 JSON 时保存接口直接 400，
  **绝不覆盖用户手写的文件**。
- 环境变量：`ENV_MAP` 白名单（`AUTOTEST_DATA_DIR` / `AUTOTEST_ARTIFACTS_DIR` / `AUTOTEST_ADB` /
  `AUTOTEST_HOST` / `AUTOTEST_PORT` / `AUTOTEST_TOKEN` / `AUTOTEST_SCRCPY_JAR`），优先级最高；
  `AUTOTEST_CONFIG` 只用于选配置文件路径，不参与值合并。

**启动项（bootstrap，`config.BOOTSTRAP_KEYS`）**：打开数据库之前就要确定的值——
`runtime.data_dir`、`runtime.artifacts_dir`、`runtime.frontend_dir`、`server.host`、`server.port`。

- 首次载入时**冻结**（`_BOOTSTRAP_LOCKED`）：运行中 reload 文件不会改变它们，只记 warning；
  `POST /api/settings/reload` 返回 `restart_required` 列出"改了但要重启"的启动项。
- 设置页只读展示；`PUT/DELETE /api/settings` 对它们返回 `INVALID_ARGUMENT`（details 带配置文件路径与环境变量名）。
- 原因：`data_dir` 里就放着数据库文件，运行中切换会出现「DB 在旧目录、artifacts 在新目录」的分裂状态。

**旧库迁移**：v0.2.8 起配置不再写 `settings` 表；启动时 `migrate_db_settings_to_file()` 把旧行一次性写入
配置文件（启动项行忽略并告警），写成功后才删除 DB 行，失败则保留待下次重试。

`GET /api/settings` 每项附带 additive 字段 `default` / `layer`(bootstrap|runtime) / `writable` / `env`，
响应顶层附 `config_file`。`config.py` 暴露 effective()/get()/sources()/layer_of()/db_writable()/env_var_for()/
write_file_values()/bootstrap_pending_changes()。

旧 note：`engine.default_delay_before_ms` / `engine.default_delay_after_ms` 只作为新建脚本的停顿初值（见 §3），
不参与运行时兜底。
## 12. 前端页面（fe-core 与 fe-mirror 边界）

v0.2.5 路由与导航：
- 主导航四项：`/console`（控制台）、`/scripts`（脚本）、`/recorder`（录制）、`/settings`（设置）；
  顶部栏不再放页面切换，设备选择在控制台镜像卡头部。
- `/` → `/console`；`/devices` → `/console`（旧链接兼容）；`/runs` → `/scripts?tab=runs`；`/runs/:id` 保留运行详情。
- `/scripts` 用 `?tab=library|runs` 区分脚本库与运行记录，数量来自接口 `total`；`/scripts/:id` 编辑；
  `/recorder?new=physical` 自动打开新建真机录制弹框；`/recorder/:id` 详情。
- 脚本页/编辑器「运行」统一跳 `/console?tab=auto&script=<id>`，由控制台自动控制用当前设备启动；
  不再有 RunScriptModal，也不再在编辑器里收集设备/变量。

前端边界：
- 请求走 `src/api/client.ts`（axios 封装，统一错误信封提示，baseURL `/api`）；
- 类型唯一来源 `src/types/api.ts`（Lead 已写，只许向后兼容追加）；后端响应 schema 由
  `scripts/export_openapi.py` 导出到 `docs/openapi.json`，前端 `npm run types:gen` 生成
  `src/types/generated.ts`，`src/types/contract.ts` 用编译期断言校验手写类型与生成类型一致；
  CI 会跑 `export_openapi.py --check` 与 `npm run types:check` 防止漂移；
- `features/mirror/**` 提供 canvas/WebCodecs/输入映射与 RecordingBar；控制台 `MirrorStage` 复用，
  `features/console/**` 负责控制台页面与手动/自动面板；
- 录制事件实时流：前端 `src/api/useRecordingEventsWs.ts` 连接 `/ws/recordings/{id}?since_seq=`，
  WS 不可用时回退 4s 轮询；事件表内存上限 1000 条。
- 主题：`localStorage['autotest.theme'] = light|dark|system`，`<html class="dark">` 切换；不进后端设置。

## 12.1 运行期补充约定（集成期定稿）

- **鉴权范围**：`security.token` 在 v1 只用于镜像 WS（`/ws/mirror/{serial}?token=`）校验；REST 端点不做鉴权
  （默认仅绑 127.0.0.1）。REST 鉴权与 CORS 收紧闭源进 v1.1。
- **data_dir 生效时机**：`runtime.data_dir` / `runtime.artifacts_dir` 属于启动项（§11），只能改配置文件或
  环境变量，**重启进程生效**；设置页只读展示，并提示配置文件路径与环境变量名。
- **fake 设备**：`GET /api/devices` **只在环境变量 `AUTOTEST_FAKE_DEVICE=1` 时**包含 `fake-1`
  （列表项带 `"fake": true`）；默认无真机返回空列表，不把“未连接/adb 不可用”伪装成可用设备。
  `get_driver("fake-*")` 显式传参始终可用，供联调/测试。
- **环境变量层**（优先级最高）：`AUTOTEST_DATA_DIR` / `AUTOTEST_ARTIFACTS_DIR` / `AUTOTEST_ADB` /
  `AUTOTEST_HOST` / `AUTOTEST_PORT` / `AUTOTEST_TOKEN` / `AUTOTEST_SCRCPY_JAR` / `AUTOTEST_CONFIG`。
  优先级：默认 < 配置文件 < DB settings < 环境变量；启动项不参与 DB 层（§11）。
- **ScreenRecorder 产物**：设备端 `screenrecord` 录 mp4 → SIGINT 收尾 → `adb pull` → 校验 ftyp/moov → 清理设备端临时文件；
  `stop() -> (Path, size)`，失败不留损坏文件（报 DEVICE_UNAVAILABLE）。
- **录制工具条唯一入口**：控制台 → 手动控制 → 镜像录制（`RecordingBar`，固定 `mode=web`）；真机录制从录制页新建。

## 13. v1 不做（明确出界）

冷恢复/检查点、双帧一致性校验、元素吸附、图像模板匹配、OCR、批量执行、run_events 归档、
pywebview 桌面壳、截图轮询降级。触发即回复"超范围，进 v1.1 清单"。

## 14. 跨模块借用接口（并行开发的缝合线；实现方必须严格满足签名，调用方按签名 mock 测试）

### 14.1 be-driver 对外暴露
```python
# app/android/selectors.py
@dataclass
class ResolvedTarget:
    x: int; y: int
    node: "HierarchyNode | None"   # point 定位时为 None
    bounds: list[int]

class TargetNotFound(Exception): ...   # str(e) 给用户看

def resolve_target(driver, target: Target, timeout_ms: int = 0,
                   poll_interval_ms: int = 350,
                   should_abort=None) -> ResolvedTarget
    # by=point 立即返回；元素定位轮询至 timeout_ms（0=只查一次）；should_abort()->bool 可打断

def check_condition(driver, target: Target, state: str = "visible") -> bool
    # 单次检查目标是否满足 state

def wait_for(driver, target: Target, state: str, timeout_ms: int,
             poll_interval_ms: int = 350, should_abort=None) -> bool
    # 轮询等待；超时返回 False（不抛）
```
```python
# app/android/adb.py
def adb_path() -> str                    # config.find_adb 缓存
def run_adb(args: list[str], timeout_s: float = 15) -> CommandResult
def list_devices() -> list[dict]         # [{serial,state,meta...}]（detailed 状态）
def forward(serial, local, remote) / remove_forward(serial, local)
def push_file(serial, local, remote) / pull_file(serial, remote, local)
def shell_bytes(serial, command) -> bytes   # exec-out，二进制安全
```
```python
# app/android/u2_driver.py
class U2Driver:                          # 实现 Driver 协议
    def __init__(self, serial: str): ...
def create_u2_driver(serial: str) -> U2Driver
```
```python
# app/android/screen.py
def screenshot_png(driver) -> bytes      # 带 PNG 头校验
class ScreenRecorder:                    # 设备端 screenrecord → SIGINT 收尾 → adb pull
    def start(self, name: str, bit_rate: int, time_limit_s: int) -> None
    def stop(self) -> tuple[Path, int]   # (mp4 路径, 大小)
```

### 14.2 be-core 对外暴露（services/device_registry.py）
```python
FAKE_PREFIX = "fake"
def list_devices(refresh: bool = False) -> list[dict]     # 契约 §7.1 GET /api/devices 的 items
def get_driver(serial: str | None = None) -> Driver
    # serial=None/"auto" -> 唯一在线设备；serial 以 "fake" 开头 -> 注册表内 FakeDriver 单例
def acquire(serial: str, purpose: "run" | "recording" | "mirror", ref_id: str) -> None
    # 已被占用 -> raise ApiError("DEVICE_BUSY", ...)
def release(serial: str, purpose: str, ref_id: str) -> None
def occupancy(serial: str) -> list[dict]                  # [{purpose, ref_id}]
def is_busy(serial: str) -> bool
```
> run 与 recording 互斥、mirror 与两者兼容；同 purpose 重复 acquire 同 ref 幂等。

### 14.3 be-media 对外暴露（app/mirror.py）
```python
def map_normalized_to_device(serial: str, nx: float, ny: float) -> tuple[int, int] | None
    # 无活跃镜像返回 None（调用方报 INVALID_ARGUMENT）
def mirror_status(serial: str) -> dict                    # {active,width,height,rotation,...}
```
api/mirror.py：POST /api/devices/{serial}/mirror/start|stop、GET .../mirror/status、WS /ws/mirror/{serial}。

### 14.4 be-runner 对外暴露
```python
# app/runner/engine.py
def start_run(run_id: int) -> None          # 线程池里跑；幂等（已 running 忽略）
# app/runner/controls.py
def request_pause(run_id) / request_resume(run_id) / request_stop(run_id)
def request_debug(run_id, action: str, step_id: str | None)
def run_control_state(run_id) -> dict
# app/events.py
class RunEventBus:  @staticmethod emit(run_id, type, payload) -> int   # 返回 seq
                    @staticmethod subscribe(run_id) -> asyncio.Queue   # WS 用
                    @staticmethod replay(run_id, since_seq) -> list
```

### 14.5 be-report 对外暴露
```python
# app/recorder/session.py
class WebRecorderSession:   # 实现 recorder.hooks.RecorderSessionLike
    def __init__(self, recording_id: str, serial: str, driver_getter): ...
class PhysicalRecorder:
    def __init__(self, recording_id: str, serial: str, driver_getter): ...
    def start(self) / stop(self)
# app/report/builder.py
def build_report(run_id: int, include_video: bool = False) -> dict   # {report_id,path,size,url}
```

### 14.6 集成规则
- 所有跨模块 import 按上述签名写；对方文件暂不存在时**不阻塞自己开发**：测试里 monkeypatch/自建桩；
- main.py（be-core）用动态路由发现：遍历 app.api 包内模块，把 `router`（HTTP，可带 `/api` prefix）与 `ws_router`（WS，挂根路径）自动 include；新增模块不要再用绝对 `/api/...` 路径，也不要让 WS 走 `/api` 前缀；
- 每人只跑自己的测试：`uv run pytest tests/test_<域>_*.py -q`；全量 pytest 由 Lead 在集成时跑；
- 完成后向 Lead 发消息：改动文件清单 + 测试结果 + 已知坑。

