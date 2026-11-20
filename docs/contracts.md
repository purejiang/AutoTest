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
    builder.py         [be-report] 报告生成（zip: index.html/junit.xml/screenshots）
    templates/report.html [be-report]
  capture/
    logcat.py          [be-report] logcat 采集（运行期 + 手动）
    network.py         [be-report] mitmproxy 插件（可选，M8，可不做）
frontend/src/
  main.tsx/App.tsx/router/layout/theme   [fe-core]
  api/client.ts  api/hooks.ts            [fe-core]
  types/api.ts                          [Lead]
  store/*                               [fe-core]
  lib/format.ts                         [fe-core]
  features/devices/**  scripts/**  runs/**  recorder/**  settings/**  [fe-core]
  features/mirror/**                    [fe-mirror] 镜像画布/WebCodecs/输入映射/录制工具条
  lib/webcodecs.ts  lib/h264.ts  lib/mirrorWs.ts  [fe-mirror]
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
表结构以 `backend/app/models.py`（Lead 已写）为准——scripts/runs/step_results/run_events/
artifacts/recordings/recording_events/reports/settings，索引见 models.py。
状态枚举：
- run.status：`queued|running|paused|stopping|succeeded|failed|aborted|stopped|interrupted`
- step_results.status：`passed|failed|skipped|error`
- recording.status：`recording|paused|stopped`
- artifact.kind：`screenshot|video|logcat|hierarchy|report|screenrecord|network`

## 3. 脚本 JSON（schema_version=1，唯一数据文件格式）

结构校验用 `app.schemas`（Lead 已写，12 类节点 params 见其模型定义）；语义校验（id 唯一、
goto 目标、sub_script 存在性 warning）用 `schemas.validate_script()`。

- `variables`：`{name: str}`，运行时可被请求 `variables` 覆盖；步骤内字符串字段支持 `{{var}}` 插值（引擎做）。
- 节点公共字段：id/name/type/description/enabled/when/timeout_ms/delay_before_ms/delay_after_ms/
  retry/on_fail/screenshot_on_fail/rerun_safety/tags/params。
- Target：`{"by": "...", "value": "...", "index": 0}` 或 `{"by": "point", "x": 540, "y": 1200}`。
- when：`{"target", "mode": "require|optional", "timeout_ms", "state": "visible|gone"}`；
  require=超时未出现→节点失败走 on_fail；optional=未出现→skipped 不算失败；轮询 350ms，禁用阻塞等待。
- 断言失败走 on_fail 但**不重试动作**；sub_script 嵌套 ≤5；click 类 target 解析失败→可重试。

## 4. 驱动接口（`app/android/driver.py`，Lead 已写）

同步接口。所有注入动作的录制钩子由 **API 层**调用，驱动层不感知录制。
- `HierarchyNode`：精简树（class_name/text/resource_id/content_desc/package/bounds/clickable/enabled/visible/children）。
- `get_driver(serial) -> Driver` 由 `services/device_registry.py` 缓存；`serial=None/"auto"` 取唯一在线设备。
- `FakeDriver`：内存假设备（假层级树 + 调用记录 + 可编程 shell），供引擎/前端联调与单测，不依赖真机。
- u2 实现（be-driver 的 u2_driver.py）必须满足同名语义；`clear_text` 用 u2 能力，失败时长按全选删除兜底。

## 5. 运行事件（run_events.type）

| type | payload 关键字段 |
|---|---|
| run_started | {script_id, script_name, device_serial, total_steps} |
| run_state | {status, step_index?} |
| step_started | {step_id, name, type, index, attempt} |
| step_finished | {step_id, status, duration_ms, attempts, error?, output?} |
| step_skipped | {step_id, reason: when_not_met\|disabled\|after_fail} |
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

## 6. WebSocket（FastAPI 原生 WebSocket，路径挂在根，不走 /api）

### 6.1 `/ws/runs/{run_id}?since_seq=0`
连接后先发 `{"kind":"state","run_id":1,"status":"running","last_seq":12}`，然后补发 since_seq 之后的历史，再实时推送。
事件帧：`{"kind":"event","seq":13,"type":"step_started","ts":"ISO","payload":{}}`。
client→server：`{"kind":"ping"}`→`{"kind":"pong"}`；`{"kind":"ack","last_seq":13}`（仅记 meta）。

### 6.2 `/ws/mirror/{serial}?token=`
1) 首条 text 为 hello：`{"kind":"hello","serial","session_id","width","height","rotation","codec":"avc1.x","sps_b64","max_size","max_fps"}`。
2) 视频帧为 binary：`[u64 BE pts_us][u32 BE payload_len][Annex B payload]`（12 字节头）。
3) 控制 text：server→`{"kind":"config",...}`（参数变化/旋转）、`{"kind":"flush","reason":"keyframe_drop"}`、
   `{"kind":"error","code","message"}`、`{"kind":"stats","fps","dropped"}`；client→`{"kind":"request_keyframe"}`、`{"kind":"ping"}`。
4) 背压：消费者慢→丢帧到下一 IDR 边界，补发整 GOP，先发 flush。
5) 坐标：镜像页控制走 REST（§7.1），带归一化坐标 nx,ny∈[0,1]（相对视频帧），后端按镜像几何反算设备像素。
   归一化是前端唯一坐标表达。
6) 无 WebCodecs：前端显示引导页，后端**不**降级截图轮询。

### 6.3 `/ws/recordings/{recording_id}?since_seq=0`
`{"kind":"event","seq","type","payload","confidence","ts"}` / `{"kind":"state","status","event_count"}`；ping/pong 同上。

## 7. REST 端点（/api 前缀；JSON；请求体见 app/api_schemas.py）

### 7.1 设备与手动控制（be-core）
GET /api/devices；POST /api/devices/connect {address}；POST /api/devices/{serial}/disconnect；
GET /api/devices/{serial}/info|hierarchy?raw=|screenshot(png)|apps?keyword=&type=|logcat?lines=200；
POST /api/devices/{serial}/tap|long_press|swipe|key|text|app|shell；
POST /api/devices/{serial}/screenrecord/start {name,bit_rate,time_limit_s} / stop；
POST /api/devices/{serial}/network/start|stop（未启用→UNSUPPORTED）；
POST /api/devices/{serial}/mirror/start|stop；GET /api/devices/{serial}/mirror/status。
> 录制挂钩：serial 有活跃录制时注入动作必须经 `app.recorder.hooks` 留痕（on_before_pointer→注入→on_action）。

### 7.2 脚本（be-core）
GET /api/scripts?query=&limit=&offset=；POST /api/scripts；GET/PUT/DELETE /api/scripts/{id}；
POST /api/scripts/{id}/duplicate；POST /api/scripts/validate {data}；
GET /api/scripts/{id}/export（json 附件）；POST /api/scripts/import（multipart 或 {data}）；
POST /api/scripts/{id}/run {serial,...}（快捷=POST /api/runs）。

### 7.3 运行（be-runner）
POST /api/runs {script_id,serial?,variables?,debug?}（占用/录制中→409）；
GET /api/runs?status=&script_id=&limit=&offset=；GET /api/runs/{id}（含 steps+artifacts 摘要）；
DELETE /api/runs/{id}；GET /api/runs/{id}/events?since_seq=&limit=；
POST /api/runs/{id}/pause | resume-check | resume | stop | debug {action,step_id?}。

### 7.4 录制（be-report）
POST /api/recordings {name?,serial,mode}；GET /api/recordings?status=&limit=；
GET /api/recordings/{id}；POST /api/recordings/{id}/pause|resume|stop；
GET /api/recordings/{id}/events?since_seq=&limit=；DELETE /api/recordings/{id}/events/{seq}；
POST /api/recordings/{id}/to_script {name?,include_kinds?,exclude_seqs?}；DELETE /api/recordings/{id}。

### 7.5 报告与维护（be-report）
POST /api/runs/{id}/report {include_video?}；GET /api/reports?run_id=；
GET /api/reports/{id}/download（zip，支持 Range）；DELETE /api/reports/{id}；
GET /api/maintenance/stats；POST /api/maintenance/cleanup {retention_days?,dry_run?}。

### 7.6 设置（be-core）
GET /api/settings → {effective, sources}；PUT /api/settings {values}（null=删该层覆盖）；
DELETE /api/settings/{key}；POST /api/settings/reset；
GET /api/settings/scrcpy/options（白名单+校验，渲染表单用）；
GET /api/health → {status,version,db,adb:{path,version,devices},data_dir}。

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
物理录制：`app.android.getevent.stream_events(serial, on_touch, on_key, stop_event)` 产出
`TouchSample{t,x,y,down,tracking_id,pressure}` / `KeyEvent{t,keycode,down}`，PhysicalRecorder 消费并转换。

## 9. 时间线与三环保护（be-runner 必做）

- 轮询型等待统一 350ms（config engine.poll_interval_ms）；所有等待可被 pause/stop 打断（≥每 350ms 检查）；
- 节点 timeout_ms（默认 settings.default_timeout_ms，上限 600s）→ StepTimeout；
- 运行总时长 engine.max_total_ms（默认 30min，**活跃时间**口径，paused 不计）；
- 步骤总预算 engine.max_steps（默认 2000，含 retry/goto 重跳）；
- goto 重入重置目标节点及其后的 retry 计数；重跑当前节点清零；
- stop 优先级最高：正在执行的动作最多等 5s 后强切（节点 failed，error.code=STOPPED）；
- pause/resume/step/run_from 由 controls 状态机管，run 从线程池执行（asyncio.to_thread 包同步引擎）。

## 10. scrcpy 镜像（be-media 必做）

- 固定 scrcpy-server **v5.0.1**，jar 放 `backend/vendor/scrcpy-server-v5.0.1.jar`，
  SHA256 记录在 `backend/vendor/scrcpy.lock.json`（下载脚本写 `scripts/fetch_scrcpy.py`，Lead 已建 scripts/ 目录）；
- 参数：`--no-audio --no-playback`，video_codec=h264，max_size/max_fps/video_bit_rate/crop 白名单来自设置；
- socket：`localabstract:autotest_scrcpy_<pid>_<rand>` 唯一；按序连接 video（v5 先 video 后 control）；
  启动后断言 socket 数量与顺序，不符→SCRCPY_FAILED+清理；
- Python 最小帧解析：12 字节头 + Annex B；SPS/PPS 缓存；参数变化→config 消息；
- 旋转：视频尺寸变化→新 SPS→重发 config；stream→device 逆映射由 MirrorSession 维护（含 max_size/crop/rotation）；
- 崩溃/断线自动重启（≤3 次/分钟），WS 发 error 保持连接；
- 启动自检：socket 数、首帧 ≤8s、codec 字符串从 SPS 解析；
- 孤儿清理：只清理本进程历史 session 的 localabstract forward，不动别人的。

## 11. 配置三级（be-core）

优先级：DB(settings 表) > 文件(`$AUTOTEST_CONFIG` 或 `~/.autotest/config.json`) > 代码默认。
`config.py`（Lead 已写）暴露 effective()/get()/sources()/set_db_overrides()；settings API 只写 DB 层。
默认值清单见 config.DEFAULTS（engine/scrcpy/recording/capture/retention/security/server）。

## 12. 前端页面（fe-core 与 fe-mirror 边界）

路由：`/devices`（镜像+控制+录制工具条）、`/scripts`、`/scripts/:id`（节点列表+表单+JSON 源码+校验）、
`/runs`、`/runs/:id`（实时监控）、`/recorder`（录制列表+确认清单+转脚本）、`/settings`。
- 请求走 `src/api/client.ts`（fetch 封装，统一错误信封提示，baseURL `/api`）；
- 类型唯一来源 `src/types/api.ts`（Lead 已写，只许向后兼容追加）；
- `features/mirror/**` 由 fe-mirror 全权负责（canvas+WebCodecs+输入映射+录制工具条 UI 骨架），
  只依赖 types/api.ts 与 api/client.ts，不 import fe-core 的 features；fe-core 在 /devices 页给 mirror 留挂载点；
- 录制工具条（开始/暂停/停止/事件数）在 mirror 页，调 §7.4 REST。

## 12.1 运行期补充约定（集成期定稿）

- **鉴权范围**：`security.token` 在 v1 只用于镜像 WS（`/ws/mirror/{serial}?token=`）校验；REST 端点不做鉴权
  （默认仅绑 127.0.0.1）。REST 鉴权与 CORS 收紧闭源进 v1.1。
- **data_dir 生效时机**：`runtime.data_dir` / `runtime.artifacts_dir` 修改后**重启进程生效**（DB 引擎在 lifespan
  早期打开，不热切换）。设置页需给出提示文案。
- **fake 设备**：`GET /api/devices` 仅在「无真实在线设备」或「环境变量 `AUTOTEST_FAKE_DEVICE=1`」时包含 `fake-1`
  （列表项带 `"fake": true`）；`get_driver("fake-*")` 显式传参始终可用，供联调/测试。
- **环境变量层**（优先级最高）：`AUTOTEST_DATA_DIR` / `AUTOTEST_ARTIFACTS_DIR` / `AUTOTEST_ADB` /
  `AUTOTEST_HOST` / `AUTOTEST_PORT` / `AUTOTEST_TOKEN` / `AUTOTEST_SCRCPY_JAR` / `AUTOTEST_CONFIG`。
  优先级：默认 < 配置文件 < DB settings < 环境变量。
- **ScreenRecorder 产物**：设备端 `screenrecord` 录 mp4 → SIGINT 收尾 → `adb pull` → 校验 ftyp/moov → 清理设备端临时文件；
  `stop() -> (Path, size)`，失败不留损坏文件（报 DEVICE_UNAVAILABLE）。
- **录制工具条唯一入口**：镜像面板内（`RecordingBar`）；设备页不再放第二个录制入口。

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
class ScreenRecorder:                    # exec-out screenrecord 流式拉取
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
- main.py（be-core）用动态路由发现：遍历 app.api 包内模块，把含 `router` 属性的自动 include；
- 每人只跑自己的测试：`uv run pytest tests/test_<域>_*.py -q`；全量 pytest 由 Lead 在集成时跑；
- 完成后向 Lead 发消息：改动文件清单 + 测试结果 + 已知坑。

