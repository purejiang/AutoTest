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
| 应用启动/停止/清数据/重启 | ✅ | `app restart` 用于脚本开头清状态最可靠 |
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
| swipe | 约等于 `duration_ms` + 30ms | 按 duration 分配 MOVE 间隔，与原生手指滑动一致 |

同时修掉两处隐藏的每次请求开销：
- `get_driver()` 曾**每次请求**都做一次 adb 枚举 + u2 存活 RPC（195ms）→ 改为存活结果 2s TTL 缓存（0ms）；
- 列表接口曾对每台设备调 `driver.info()`（3 次 RPC ≈580ms，5s 轮询一次拖垮页面）→ 改为纯 adb 批量 getprop（30ms×60s 缓存）+ 列表 1.2s 结果缓存；
- `uiautomator2.app_current()` 5.4s → adb dumpsys 0.13s（见 §3）。

前端渲染：canvas 的 `devicePixelRatio` 上限 1.5（Retina 下 4 倍像素的 drawImage 在集显机器上吃帧时间）。

## 4. 脚本编写最佳实践（真机踩出来的）

1. **断言优先用前台包名**判断"是否在目标页面"（稳、快）；页面内容断言挑**唯一且稳定**的文案
   （如 MIUI 设置主页的 `WLAN`），避免用 "已连接/添加网络" 这类随状态变化或虚拟列表滚出屏幕的文案。
2. `text_contains` 容易被包含关系误命中（如"网络"命中"移动网络"/"网络加速"）。优先 `text` 精确匹配，
   必要时配合 `resource_id`。
3. 重复执行脚本时页面会残留上次状态：脚本开头用 `app restart`（或连续 back）把栈清到确定状态。
4. 系统设置页可能**跨进程**（如"移动网络"页由 `com.android.phone` 承载），此时前台包名断言应放宽或用元素断言。
5. 真机被人同时使用时前台会变，运行会互相干扰：跑前确认设备空闲；被抢占时断言会如实失败（这是正确行为）。

## 5. 仍需真机/真浏览器确认（回归时补）

- 浏览器端 WebCodecs 实际渲染（Chrome/Edge）：Annex B 直解与 avcC 回退两条路径、flush 后等 IDR、旋转重配。
- 镜像页鼠标操作的**落点准确性**（归一化→设备像素逆映射，含 `max_size`/`crop` 组合）。
- 物理录制的 **2 点标定**精度与旋转时间线（当前只验证了触摸设备探测与事件解析）。
- 多设备并发（当前 registry 支持多 serial，未做并发压测）。
- Windows 平台全流程（adb 路径探测、打包、录屏/pull 路径差异）。
