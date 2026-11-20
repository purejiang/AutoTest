# AutoTest · Android 自动化测试工具

本地单机的 Android UI 自动化测试平台：**连接手机 → 脚本编排 / 真实操作录制 → 顺序执行 → 测试报告**。
后端 Python（adb + uiautomator2 + scrcpy），前端 React，同一套代码跑 macOS / Windows / Linux。

## 功能

| 能力 | 说明 |
|---|---|
| 设备控制 | 点击 / 长按 / 滑动 / 拖拽 / 输入 / 系统键（返回·桌面·最近任务·音量·电源）/ 启停清应用 / shell / 截图 / 录屏 / logcat |
| 实时镜像 | scrcpy 服务端 + WebCodecs 低延迟解码，鼠标直接在镜像上操作设备（归一化坐标，后端反算设备像素） |
| 步骤脚本 | JSON 数据文件，顺序执行；每个节点独立配置触发条件、定位方式、超时、延迟、重试、失败策略、节点名/注释、重跑安全性 |
| 12 类节点 | click / long_click / swipe / drag / input / key / wait / app / shell / assert / sub_script / screenshot |
| 录制 | **镜像网页操作录制（主）** + 物理手机操作录制（辅助，getevent）；支持暂停；录制结果可一键转脚本；脚本可作为节点嵌入其他脚本 |
| 持久化 | 脚本、运行记录、步骤结果、事件流、产物、报告全部存 SQLite（WAL），带 CRUD |
| 报告 | 一键生成 zip（HTML 汇总 + JUnit XML + 失败截图），支持逐节点耗时与失败原因 |
| 调试 | 断点、单步、从指定节点重跑；暂停后解锁手动控制（干预留痕） |
| 治理 | 30 天保留策略、清理统计、报告体积上限（不含视频） |

## 快速开始

### 前置

- Python **3.12**（用 [uv](https://docs.astral.sh/uv/) 管理，无需系统 Python 升级）
- Node.js **20+**
- Android platform-tools（adb）—— macOS 常见路径 `~/Library/Android/sdk/platform-tools/adb`，Windows `%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe`
- 手机开 USB 调试并授权（`adb devices` 能看到 `device`）

### 安装

```bash
# macOS / Linux
git clone <repo> AutoTest && cd AutoTest
sh scripts/setup.sh          # uv sync + npm install + 下载 scrcpy-server + 环境自检

# Windows (PowerShell)
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

### 启动

```bash
# 一条命令同时起后端(8787) + 前端(5173)，Ctrl+C 一起退出
sh scripts/dev.sh            # Windows: scripts\dev.ps1
```

打开 http://127.0.0.1:5173 。生产用法：`sh scripts/build.sh` 后只跑后端，前端静态资源由后端托管在 http://127.0.0.1:8787 。

### 使用流程

1. **设备页**：确认设备在线 → 启动镜像 → 可直接在镜像上点按操作；需要录制时点"开始录制"。
2. **脚本页**：新建脚本，手动加节点（表单 + JSON 源码双向编辑，含校验），或把录制结果一键转成脚本。
3. **运行页**：选择设备执行，实时看步骤/事件/截图；失败可暂停、干预、从节点重跑。
4. **报告**：运行结束一键生成 zip，含 JUnit XML 与失败截图。

## 已验证（真机 Xiaomi 23127PN0CC / Android 16 / 1200×2670）

| 能力 | 状态 |
|---|---|
| 设备发现·信息·层级·截图·shell·logcat·应用列表 | ✅ 实测 |
| 脚本执行（app/wait/click/assert/swipe/shell/screenshot/key） | ✅ 10 步 succeeded |
| 失败重试 · 失败截图 · on_fail abort/continue/goto | ✅ 实测 |
| 暂停 = 干预模式（暂停期手动控制 + `manual_intervention` 留痕）· 预检 · 热恢复 | ✅ 实测 |
| scrcpy 实时镜像（718×1600 / avc1.64002A / 首帧 IDR+SPS+PPS） | ✅ 后端 WS 收帧实测 |
| 镜像页操作直连 scrcpy control 通道（**点击端到端 4~40ms**） | ✅ 真机实测，坐标 3/3 落点正确 |
| web 录制 → 转脚本 → 回放 | ✅ 实测 succeeded |
| 物理录制（触摸设备探测 + 事件解析 + 标定字段） | ✅ 探测实测（标定精度待回归） |
| 设备录屏 mp4（可播放）· 报告 zip（HTML+JUnit+截图）· 产物直链 | ✅ 实测 |
| PyInstaller 单文件打包（36MB） | ✅ 实测可启动 |

> 浏览器侧 WebCodecs 渲染、镜像落点准确性、Windows 全流程仍需回归，清单见
> `docs/device-testing-checklist.md`；联调实测细节与踩坑见 `docs/real-device-notes.md`。

## 项目结构

```
backend/
  app/
    config.py db.py models.py schemas.py api_schemas.py errors.py logs.py
    main.py deps.py
    services/     设备注册表、脚本服务、设置服务
    api/          devices scripts runs recorder reports settings mirror
    android/      adb hierarchy selectors actions u2_driver screen scrcpy getevent touchmap driver
    runner/       engine context controls executors
    recorder/     hooks session convert
    report/       builder + templates
    capture/      logcat network
  tests/          pytest（全部用 FakeDriver，不依赖真机）
frontend/src/     React + TS + AntD 5 + TanStack Query + WebCodecs
docs/contracts.md 唯一契约（错误码/DB/脚本格式/驱动接口/WS/端点/配置）
samples/          示例脚本
scripts/          setup / dev / build / 环境自检 / scrcpy 下载
```

## 关键设计（详见 `docs/contracts.md`）

- **分层**：tools(adb/u2/scrcpy) → engine(顺序执行) → 数据/逻辑 → API → Web。
- **等待全部自轮询**（350ms，可被暂停/停止打断），禁用 uiautomator2 阻塞等待 API，保证暂停响应 ≤5s。
- **暂停 = 干预模式**：暂停后解锁手动控制，手动操作全部留痕（`manual_intervention`），恢复前做文字化预检。
- **重跑安全性**：节点标记 `rerun_safety ∈ {safe,destructive,stateful}`，破坏性节点禁止"重跑当前节点"。
- **镜像**：scrcpy-server 版本与 SHA256 锁定；`--no-audio`；unique localabstract；背压丢帧到下一 IDR 并通知前端 flush；旋转/参数变化重配解码器。
- **契约冻结**：`docs/contracts.md` 是唯一事实来源，改动需先改文档再改代码。

## 配置（三级：默认 < 配置文件 < 数据库）

- 配置文件：`~/.autotest/config.json`（或 `AUTOTEST_CONFIG` 指定），形如 `{"server": {"port": 8787}, "scrcpy": {"max_size": 1200}}`
- 数据库层：设置页写入（优先级最高）
- 数据目录：`~/.autotest/data`（`runtime.data_dir` 可改），含 DB、artifacts、reports、日志

## 开发

```bash
cd backend && uv run pytest -q                 # 后端测试（无需真机）
cd backend && uv run uvicorn app.main:app --reload --port 8787
cd frontend && npm run dev                      # 前端 dev（代理到 8787）
cd frontend && npm run typecheck                # 类型检查
```

无真机也能开发/联调：设备注册表会把 serial 以 `fake` 开头的设备路由到内存 `FakeDriver`（假层级树 + 动作记录），
前端与引擎都可用它跑通全链路。

## 常见问题

- **adb 找不到**：设置页填 adb 绝对路径，或把 platform-tools 加进 PATH。
- **设备 offline/unauthorized**：重新插拔、手机上确认授权、`adb kill-server && adb start-server`。
- **镜像黑屏/报错**：确认 scrcpy-server jar 已下载（`sh scripts/setup.sh`），在设置页调小 `max_size`(如 800) 与码率。
- **浏览器不支持 WebCodecs**（Firefox 旧版/Safari 旧版）：镜像页会提示；此时用**物理录制**模式（手机真实操作，通过 getevent 采集）。
- **录制没有出现节点**：检查录制的设备是否就是操作的那台；物理录制需要先通过触摸设备确认与 2 点标定。
- **运行时提示 409 DEVICE_BUSY**：设备被运行/录制占用；手动控制（含 shell）在运行中会被拒绝，暂停运行后即可操作（并会留痕）。
- **断言失败但页面看着是对的**：优先用「前台包名」或 `text` 精确匹配；`text_contains` 容易命中包含关系的其它文案；
  系统设置页可能跨进程（包名变化），详见 `docs/real-device-notes.md` 的脚本编写最佳实践。
