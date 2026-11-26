# 真机联调结论（macOS + Xiaomi 23127PN0CC / Android 16 / SDK 36 / 1200x2670）

> 时间：v1 集成期。设备通过 USB 直连（`<serial>`，已脱敏）。
> 本文记录**实测事实**与由此产生的实现决策，供后续维护/回归参考。

## 1. 已验证通过的能力（真机）

| 能力 | 结果 | 备注 |
|---|---|---|
| 设备发现/信息 | ✅ | 列表 0.48s 返回品牌/型号/Android/分辨率/密度（一次 adb shell 批量 getprop，60s 缓存） |
| 层级 dump（含原始 XML） | ✅ | 83 节点（MIUI 桌面）/ 40KB 原始 XML；Unity 游戏内层级很浅属正常 |
| 截图 | ✅ | 1200x2670 PNG，~2.6MB |
| 点击/长按/滑动/返回键 | ✅ | 坐标归一化与绝对坐标两条路径均验证 |
| 应用启动/停止/清数据/重启/授权 | ✅ | `app restart` 用于脚本开头清状态最可靠；`app clear` 后自动重新授予运行时权限，`app grant` 单独授权 |
| shell / logcat / 应用列表 | ✅ | `type=all|system|third_party`（`third` 为兼容别名） |
| 设备录屏 mp4 | ✅ | 3s → 875KB，ftyp+moov 校验通过，可播放 |
| scrcpy 镜像 | ✅ | 718x1600 / avc1.64002A；首帧 IDR+SPS+PPS；WS 收帧 20/20；stop 无残留 |
| 脚本执行（8 类节点） | ✅ | app/wait/click/assert/swipe/shell/screenshot/key 全绿，10 步 succeeded |
| 失败重试/失败截图/on_fail abort | ✅ | 不存在元素：attempts=2、1 条 step_retry、自动截图产物 |
| 暂停=干预模式 | ✅ | 暂停延迟 0.02~0.35s；暂停期间手动控制 200；`manual_intervention` 事件落库 |
| 恢复预检 + 热恢复 | ✅ | `POST resume-check` 返回前台/存活/破坏性节点；resume 后继续执行至成功 |
| web 录制→转脚本→回放 | ✅ | tap/swipe/key 3 事件；置信度 high；生成脚本校验通过；回放 succeeded |
| 物理录制探测 | ✅ | 识别 `/dev/input/event7 synaptics_tcm_touch`，meta 记录触摸设备/标定/时钟锚点 |
| 报告生成/下载 | ✅ | zip 含 index.html + junit.xml + steps.json + 截图；产物直链 `/artifacts/...` 可用 |
| 打包（PyInstaller） | ✅ | 36MB 单文件；启动后路由/静态/SPA 均正常 |

## 2. scrcpy v5.0.1 协议（实测 + 官方源码核对）

- 资产名 **无 `.jar` 后缀**：`scrcpy-server-v5.0.1`，官方 SHA256
  `764eb6f79811d5211fe9df341120882ba9994c7a61b897d7bf3fb662e53bc536`（733930 字节）。
- 合法参数：`log_level max_size max_fps video_bit_rate crop display_id audio video control
  tunnel_forward send_device_meta send_frame_meta send_dummy_byte send_stream_meta clipboard_autosync
  power_on cleanup scid`。**没有 `socket=`**。
- socket 名由 `scid` 派生：`localabstract:scrcpy_<scid:%08x>`（`scid` 参数按**十六进制**解析）。
- 字节流顺序：
  `[1B dummy=0x00][64B device name][4B codec id "h264"][12B session meta: flags|w|h]`，
  之后每包 `[12B: pts_and_flags u64 BE | size u32 BE][size B Annex B]`；
  flags：bit61=KEY_FRAME，bit62=CONFIG（CONFIG 包=SPS/PPS，需拼到下一视频帧前）。
- 连接顺序：**video → control**（只连 video 时 server 不出帧）。
- jar 必须常驻设备 `/data/local/tmp/`（每次 start 前校验/重推；清理时不要删，否则下次启动
  `ClassNotFoundException` → app_process abort，shell 只显示 "Aborted"）。

## 3. 性能实测（决定实现取舍）

| 调用 | 耗时 | 决策 |
|---|---|---|
| `uiautomator2.app_current()` | **5.4s** | 弃用 → 改走 `adb shell dumpsys window \| grep mCurrentFocus`（**0.13s**），u2 仅兜底 |
| `dump_hierarchy` | 0.2s | 保持 u2 |
| 点击注入 | 0.19s | 保持 u2 |
| `adb devices -l` + 批量 getprop | 0.13s + 0.35s | 设备列表首屏用它，u2 `info()` 只在详情页 |
| 暂停响应 | 0.02~0.35s | 满足 ≤5s 契约 |

> 结论：**前台应用判断绝不能走 u2**，否则每个 assert foreground / app 节点都会多 5 秒，
> 且会在 step 超时上误报 STEP_TIMEOUT（此坑已修并记录在 u2_driver.current_app 注释）。

## 3.1 操作延迟优化（v1.1 集成期）

镜像页的操作**不再走 uiautomator2**，改为直发 scrcpy control socket（`INJECT_TOUCH_EVENT` 等，
见 `app/android/scrcpy.py` 的 `build_inject_*`）：设备端直接注入，实测端到端 **4~40ms**。

| 路径 | tap 端到端 | 说明 |
|---|---|---|
| 镜像开启（scrcpy control） | **4~40ms** | 坐标：先按 `crop_rect` 逆变换到视频帧坐标，screen_size 必须**精确等于**当前视频尺寸，否则 server 静默丢弃事件 |
| 镜像未开（uiautomator2 兜底） | 200ms~1.2s | 脚本执行仍走 u2（需要可访问性语义） |
| key / text（control） | 4~14ms | text 单条 ≤300 字符自动切片 |
| swipe（一次性 API） | 约等于 `duration_ms` + 30ms | 按 duration 分配 MOVE 间隔，与原生手指滑动一致 |
| **镜像页拖动（流式）** | **单条消息 3~5ms** | `POST /touch` 逐条下发 down/move/up：按下即生效、移动实时跟手（不再等松手） |

同时修掉两处隐藏的每次请求开销：
- `get_driver()` 曾**每次请求**都做一次 adb 枚举 + u2 存活 RPC（195ms）→ 改为存活结果 2s TTL 缓存（0ms）；
- 列表接口曾对每台设备调 `driver.info()`（3 次 RPC ≈580ms，5s 轮询一次拖垮页面）→ 改为纯 adb 批量 getprop（30ms×60s 缓存）+ 列表 1.2s 结果缓存；
- `uiautomator2.app_current()` 5.4s → adb dumpsys 0.13s（见 §3）。

前端渲染：canvas 的 `devicePixelRatio` 上限 1.5（Retina 下 4 倍像素的 drawImage 在集显机器上吃帧时间）。

**流式触摸（v1.1 修复）**：旧实现把整段手势缓存在浏览器里、松手才发一条 `/swipe`，导致"按住拖动过程中
手机毫无反应"。现改为：
- `pointerdown` → 立即 `POST /touch {action:"down"}`（设备马上有按下反馈）
- `pointermove` → 持续 `{"action":"move"}`；前端**同一时刻只允许一条在飞、只保留最新位置**，避免请求堆积
- `pointerup` → 等 in-flight 结束后发 `{"action":"up"}`，后端把整段手势归并成**一条**录制事件
  （tap / long_press / swipe / drag，按位移与时长判定），录制与转脚本语义不变
- 无镜像会话（无 control socket）时 `/touch` 返回 `UNSUPPORTED`，前端自动回落到"松手后一次性手势"，
  并在状态行提示一次，不影响无镜像场景
- **UP 一定送达**（v0.2.6 加固）：设备端只认 down/move/up，漏掉 up 就会一直保持按压。除了
  `pointerup`，还有三层兜底——① 画布指针捕获被浏览器丢掉时，挂在 window 捕获阶段的
  `pointerup/pointercancel` 负责补 UP；② 鼠标已经在窗口外松开（浏览器不再发 pointerup）时，
  下一次 `pointermove/pointerrawupdate` 看到 `buttons=0` 就当场补 UP；③ 拖动中窗口失焦 / 页面切后台 /
  组件卸载时用最后位置主动补 UP。WS 的 up 发不出去时再静默走一次 REST `/touch`。
  （现象对照：以前在手机区域外松手，页面会一直以为还按着，回到画布移动仍在拖设备。）
真机验证：down+up = 点击（2/2 正确）、down+move…+up 能把设置页从「我的设备/WLAN」滚到「锁屏/显示与亮度」、
录制得到 `key → swipe → tap` 三条事件并成功转脚本。

### 3.1.1 镜像传输性能（2026-11 打磨）

- **Python 端每帧 NAL 扫描是最大隐藏开销**：40KB payload 的 `split_nals()` 在本机约 15ms；
  旧实现 reader + `_ingest` 各扫一遍，20–30fps 时接近吃满一个核。现在 P 帧只依赖 v5 帧头的
  keyframe flag，不扫 NAL；仅 config 包、关键帧、首帧、或首 NAL 为 SPS/PPS/IDR 时扫描。
- WebSocket 关闭 per-message-deflate（H.264 本身已压缩）：实测帧到达抖动 std 15ms → 8ms。
- `stats` 从 send 循环里的每帧 `wait_for(timeout=5)` 改为独立 5s 定时任务，避免每帧创建 timeout task。
- 触摸 `move` 不再每条开 SQLite session（仅 run 占用/暂停判断才需要 DB）。
- 2014 款 i7（2 核 4 线程）+ 1200 长边时，设置页「流畅优先」（1200/60/10M）明显比 1600 稳；
  当前手机游戏源本身约 20–30fps，镜像不能凭空变 60。

## 3.2 录制→回放保真度（v1.1 修复，真机验证一致）

用户反馈「录制出来的脚本运行起来和操作不一样」，真机复现后定位到 4 个缺陷并修复：

| # | 缺陷 | 根因 | 修复 |
|---|---|---|---|
| 1 | 元素归因退化成坐标 | 流式触摸「先注入 down 再同步 dump」，抓到的是动作之后的界面；系统设置行是「可点击父容器 + 无点击子文本」，父容器没有 text | 录制期间**后台每 500ms 采样层级**，down 时直接用最近快照（零等待、准）；归因**向下找可读子节点**（WLAN/移动网络 → `by:text`），黑色空白区才退回坐标 |
| 2 | 回放"瞬间连点" | 录制时的操作间隔被丢弃，所有节点 `delay_before_ms=0` | 按事件 `created_at` 还原间隔（上限 3s，首节点固定 800ms），转换时提示"已还原 N 个节点的延迟" |
| 3 | 回放不从同一界面开始 | `app` 事件被丢弃（旧契约 §13） | app 启动/停止/清数据**生成 app 节点**（破坏性动作标 `rerun_safety=destructive`） |
| 4 | 输入丢字/失败 | 输入节点没有目标，回放只能输入到"当前焦点"；若带上点击目标又会二次点击导致目标消失 | 输入事件**复用 5s 内最近点击的元素**做目标；转换时若紧跟在同目标点击之后则**去掉 target**（前一步已聚焦） |

真机验证（Xiaomi Android 16）：
- 点击流：录「启动设置 → WLAN → 返回 → 移动网络」，从桌面回放 → `succeeded`，最终前台 `com.android.phone` 与录制时**完全一致**；
- 输入流：录「搜索框 → 输入 wlan」，回放 `succeeded`，界面出现 `wlan` 与搜索结果；
- 节点目标全部为 `by:text` 高置信度（WLAN / 移动网络 / 搜索系统设置项）。

## 3.3 拖动不同步的根治（v1.1，真机量化验证）

现象：拖动/滚动"不同步"——手指移动了但手机几乎不滚、或滚得比手指多。

真机上量化对比（同一手势，700ms 拖 10% 屏高）：

| 投递方式 | 手机实际滚动 | 结论 |
|---|---|---|
| **8Hz（稀疏，旧行为等价）** | **0~171px** | 采样太稀，Android 判定不出连续滚动 |
| 60Hz HTTP 逐条 | 394~774px | 能滚，但受 HTTP RTT 抖动影响 |
| **WS 逐条（现在首选）** | 757px，且各元素位移一致 | 26 条消息纯开销 ~23ms（≈0.9ms/条） |

修复内容：
1. **move 改走镜像 WS**（`{"kind":"touch"}`，同一条长连接）：无 HTTP 建连/请求开销、严格有序；REST `/touch` 保留为兜底；
2. 前端 **pointermove 只入队**（不触发 React 重渲染，否则 60Hz 重渲染会把采样拖丢）；
   用 8ms 定时器按序投递，队列最多 12 个采样（快拖也不丢轨迹）；
3. 用 `PointerEvent.getCoalescedEvents()` 取回浏览器因主线程忙而合并掉的原始采样点；
4. **flush 风暴修复**：背压原来"每丢一帧发一条 flush"，控制消息优先级高于帧 → 帧被饿死（新页面黑屏数秒）。
   现改为每 500ms 最多一条 → WS 镜像测试由 3 次挂 1 次变为 15/15 稳定，真机画面也更稳；
5. 新订阅者**强制**请求一次 IDR（原来被 0.8s 节流拦下），打开镜像页立即出画面。

## 3.4 录制定位方式：默认纯坐标（v1.1，按用户要求）

游戏/自绘 UI（Unity、Flutter 自绘、视频播放器等）的无障碍树不可靠，元素定位会在回放时点错或找不到。
因此录制转脚本**默认只记绝对坐标**：

- `POST /recordings/{id}/to_script {target_mode:"point"}`（默认）→ 点击/长按全部是 `{by:"point",x,y}`；
- `target_mode:"element"` → 优先元素（text/resource_id…），失败退回坐标（旧行为，保留给系统应用等稳定界面）；
- 默认值可在设置页改：`recording.target_mode`（enum，设置页下拉；DB/文件/默认三层覆盖）；
- `point` 模式下输入事件统一改为「先点坐标聚焦 → 再输入」（自动补点击节点），避免依赖焦点状态；
- 转换提示会说明「N 个节点使用绝对坐标（换分辨率/布局变化时需重录）」。

真机验证：同一段录制分别转出两份脚本——坐标版 `click(point 304,1180)` / 元素版 `click(text="WLAN")`，
坐标版回放 `succeeded` 且最终界面与录制一致。

**滚轮**：镜像画布不再把鼠标滚轮映射成设备滑动（用户要求，避免误滑）；设备滚动请用拖动/滑动。

## 3.5 "我怎么操作，脚本就怎么运行"（v1.1，真机量化：回放后界面位置 0px 误差）

要让回放和真人操作一致，关键不是"节点类型"，而是**轨迹 + 时序 + 等待**三件事：

| 环节 | 旧实现 | 现在 |
|---|---|---|
| 滑动轨迹 | 只记起点/终点，回放用 u2 两点直线 swipe | **录完整轨迹**（down/move…/up 的每个采样点，含相对时间），脚本节点 `params.path=[{x,y,dt_ms}…]`；有活跃镜像时回放用 scrcpy control 通道逐点注入（保留轨迹/变速/末端停顿），无镜像时退化为两点 u2 swipe（u2 `swipe_points` 因 Unity/游戏设备兼容性弃用） |
| 操作间隔 | 截断到最多 3s | 上限 15s（加载/思考的等待不会被压掉），首节点 800ms |
| 瞬时动作 | tap 时长丢失 | tap/long_press 都带录制时的 `duration_ms` |

真机验证（Xiaomi Android 16）：录一段"弧形 + 变速"滑动 → 转脚本（swipe 带 9 点轨迹）→ 复位到同一界面回放：
- 回放 `succeeded`，步骤输出含 `path_points: 9`（确实走了轨迹通道）；
- **回放后 17 个可见列表项位置与录制后完全相同（最大偏差 0px）**；
- 5 秒等待被还原为 `delay_before_ms=6810ms`（旧实现会截成 3s）。

仍不支持（诚实边界）：多指手势（缩放/双指滑动）只录主指针；软键盘逐键输入按整段文本回放；
不同分辨率/旋转会改变绝对坐标的落点（坐标模式的固有限制，可用 element 模式缓解）。

## 3.6 物理录制取流排障（v0.2.7，真机修复）

2026-11 在一台 Android 12 / `input_mt_wrapper` 设备上复现“真机录制操作后 0 事件”。抓包 + 源码核对结论：

- `adb -s <serial> exec-out getevent -lt /dev/input/event3` 的输出**没有** `/dev/input/event3:` 前缀：
  `[   93856.488781] EV_ABS       ABS_MT_POSITION_X    00001516`
  旧 `_LINE_RE` 强制要求设备前缀，1054 行原始输出全部被丢弃；把前缀补上后同一抓包可解析出 79 个
  `TouchSample`。
- 该设备抬手只发 `BTN_TOUCH UP`，**不发** `ABS_MT_TRACKING_ID ffffffff`。旧状态机只在 tracking id < 0
  时结束手势，所以即使修好前缀，tap/swipe 也永远不会 finish。
- 没有触摸时 stdout 没有新行，`stop_event` 无法打断阻塞迭代；旧实现 `join(3s)` 超时，getevent 进程残留
  （日志中的 “physical recorder thread did not stop in time”）。
- legacy toolbox `getevent` 源码有 `setbuf(stdout, NULL)`，**不是 stdout 缓冲问题**；不要为此把
  `exec-out` 改成 `adb shell`。
- `-l` 标签模式下 `EV_KEY ... DOWN/UP` 的值不是十六进制，且标签表不含 `KEY_BACK` 等 Linux 键名；
  改用数字模式 `-t` 后按键与触摸共用同一套解析。

修复要点：
- 行解析兼容带/不带设备前缀；无前缀时 `device=""`，不做设备过滤。
- `BTN_TOUCH` 作为接触边界（三态；`BTN_TOUCH UP` 释放 active slot），tracking id -1 作为兜底；
  changed slots 为空时按“抬起取 active slot / 否则当前或 0 号”兜底。
- `stream_events` 用数字模式 `getevent -t <device>`；标签模式补 `DOWN=1`/`UP=0` 兼容旧抓包。
- `stream_events` 增加 watchdog：`stop_event` 置位后 terminate 进程；统计“有原始行但 0 解析事件”并
  返回错误串，`PhysicalRecorder` 写入 `recordings.meta.capture_error`。
- 详情页展示采集设备（名称/路径/屏幕）与 `capture_error`，避免再次出现“静默 0 事件”。

回归：
- 单测覆盖无前缀 label/numeric 行、只有 `BTN_TOUCH UP` 的抬手、多 slot 抬起、阻塞停止、capture_error。
- 真机：点 3 下 + 滑动 → 事件数 > 0；停止 3 秒内线程退出；拔线/杀 getevent → 详情页出现
  “触摸采集中断”。
- 旧协议 A（无 `ABS_MT_TRACKING_ID`、只靠 BTN_TOUCH 的老设备）不在本次真机验证范围；
  当前解析器保留该分支，但如需正式支持应单独回归 move 帧重复 down 的问题。

## 4. 脚本编写最佳实践（真机踩出来的）

1. **断言优先用前台包名**判断"是否在目标页面"（稳、快）；页面内容断言挑**唯一且稳定**的文案
   （如 MIUI 设置主页的 `WLAN`），避免用 "已连接/添加网络" 这类随状态变化或虚拟列表滚出屏幕的文案。
2. `text_contains` 容易被包含关系误命中（如"网络"命中"移动网络"/"网络加速"）。优先 `text` 精确匹配，
   必要时配合 `resource_id`。
3. 重复执行脚本时页面会残留上次状态：脚本开头用 `app restart`（或连续 back）把栈清到确定状态。
4. 系统设置页可能**跨进程**（如"移动网络"页由 `com.android.phone` 承载），此时前台包名断言应放宽或用元素断言。
5. **清数据会重置运行时权限**：App 冷启动会弹系统授权框（通知/存储/相机…），把登录页挡在后面，
   脚本等元素会一直等到超时。`app clear` 现在清完会自动重新 `pm grant` 全部已声明运行时权限；
   不想清数据的脚本可单独加 `app grant`。游戏自身的隐私协议/公告弹窗仍需脚本步骤处理。
6. **注入已改 adb first**：uiautomator2 的 click/swipe/press 在 Unity 常驻动画界面会等「界面空闲」约 10s
   （超过默认 8s 节点超时，表现为「点击已生效但步骤 STEP_TIMEOUT」）；驱动改用 `adb shell input`（~0.1s），
   失败才回落 u2。真机回归：点击步骤应在亚秒级完成。
7. **横屏/游戏 IME 会盖住页面按钮**：输入账号/密码后键盘（部分机型是横屏全屏提取框）仍展开，
   紧接着的「注册并登录」等按钮会被键盘挡住，点击落到键盘上（还可能打出乱字符），流程看似执行成功实则没提交。
   输入节点勾选「输入后收起键盘」（`hide_keyboard`）即可；或手动加一步 `key: back`。
8. **u2 `send_keys` 在部分机型/游戏输入框上会把文本输入成单个 "v"**：它实现为「写剪贴板 + 注入 Ctrl+V 粘贴」，
   粘贴不生效时 Ctrl+V 被当成普通按键。驱动已改为 ASCII 走 `adb input text`、中文走无障碍 `set_text`；
   同时输入节点可勾「输入后收起键盘」，避免键盘挡住后续点击。
9. 真机被人同时使用时前台会变，运行会互相干扰：跑前确认设备空闲；被抢占时断言会如实失败（这是正确行为）。

## 5. 仍需真机/真浏览器确认（回归时补）

- 浏览器端 WebCodecs 实际渲染（Chrome/Edge）：Annex B 直解与 avcC 回退两条路径、flush 后等 IDR、旋转重配。
- 镜像页鼠标操作的**落点准确性**（归一化→设备像素逆映射，含 `max_size`/`crop` 组合）。
- 物理录制的 **2 点标定**精度与旋转时间线（取流/停止/转脚本已在 v0.2.7 真机验证，标定精度与旋转仍未验证）。
- 多设备并发（当前 registry 支持多 serial，未做并发压测）。
- Windows 平台全流程（adb 路径探测、打包、录屏/pull 路径差异）。

## 镜像跟手：浏览器端实测（Xiaomi 23127PN0CC → macOS / i7-4578U / 60Hz 屏）

结论：镜像“不跟手”的最大变量是**浏览器**，不是 scrcpy 参数。用 `scripts/analyze_mirror_diag.py`
分析 `data/diag/mirror-*.jsonl` 得到的实测（同一台设备、1020×2270 / 85% / 不限帧率）：

| 时段 | 浏览器 | 活跃样本渲染 fps（中位） | 跟上期望的比例 | 判定 |
|---|---|---|---|---|
| 03:00–03:02 | Safari | 14~16 | 26%~33% | 明显跟不上（Safari 的 WebCodecs 解码路径慢） |
| 03:04–03:05 | Chrome（切换后） | 45~55 | 75%~92% | 基本跟得上 |
| 03:06–03:07 | Chrome（快速滑动） | 28~56 | 47%~93% | 仍有掉帧：85% + 不限帧率 ≈ 250MP/s 超出解码预算 |

补充事实：
- `draw_ms` 全程 0.3~0.5ms，**绘制不是瓶颈**；瓶颈在解码侧（掉帧以 burst 形式出现，丢帧计数 0→22）。
- 60Hz 屏幕下推流 120fps 时，渲染最多到 60，**约一半解码结果不会被显示**；把帧率设 60 可直接省掉这部分。
- 把缩放降到 70%（838×1868 ≈ 1.56MP）后，同样 120fps 的解码量从 250MP/s 降到 ≈188MP/s。

诊断数据来源（每 5s 一条，自动落盘）：
`data/diag/mirror-YYYYMMDD.jsonl`，`source=client` 为浏览器上报（含 `draw_ms`/`queue_size`/`display_hz`），
`source=server` 为后端 10s 打点（出帧率/发送队列/几何）。前端控制台另有 `[mirror-diag]` 行，
`__autotestMirrorDiag()` 可导出最近 120 行。
