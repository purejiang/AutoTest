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

### 启动（一键，推荐）

**只开一个进程、一个端口**，前端由后端直接托管，首次运行会自动装依赖并构建：

```bash
# macOS / Linux
sh scripts/start.sh            # 加 --open 自动开浏览器；--port 9000 换端口

# macOS 还可以：Finder 里双击项目根目录的「启动AutoTest.command」

# Windows (PowerShell)
powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Open
```

浏览器打开 **http://127.0.0.1:8787** ，终端窗口关掉即停止。

首次运行脚本会自动完成：装 uv（若缺）→ Python 3.12 + 后端依赖 → 前端依赖 + 构建 → 下载 scrcpy-server → 启动。
也可以先手动跑一次 `sh scripts/setup.sh`（Windows `scripts\setup.ps1`）做完整安装 + 环境自检。

### 版本管理（本地 git，无远程）

仓库是**纯本地** git（`git remote -v` 为空），不联网、不推送。每次改动想留档时：

```bash
sh scripts/save.sh "改了镜像布局"     # 一条命令提交当前改动
sh scripts/save.sh --log             # 看最近提交（图形化）
sh scripts/release.sh 0.1.1          # 发版：同步版本号 + 提交 + 打 tag
```

git 会忽略：`backend/.venv`、`frontend/node_modules`、`frontend/dist`、`backend/dist`（打包产物）、
`~/.autotest/data` 之外的数据文件、`__pycache__` 等；**会**跟踪 `backend/vendor/scrcpy-server-v5.0.1.jar`
（734KB，保证离线可用）。仓库约 3MB。

### 版本模型：一个产品版本，前后端同 tag

前后端在**同一个仓库**、同一个提交里，所以：

- **一个 tag = 一份完整快照**（前端 + 后端代码都在里面），不需要分别打；
- **版本号以后端为权威**（`backend/app/__init__.py`），发版时四处同步（见下）；
- 界面上显示 **后端版本 + 页面版本/提交**，随时能确认"线上页面是哪次构建、与后端是否同源"。

**前端热更**（不重启后端、不重新打 tag 也能换界面）：

```bash
cd frontend && npm run build                  # 重新构建 dist
# 方式一（源码运行）：直接覆盖仓库的 frontend/dist，浏览器硬刷新即可
# 方式二（打包单文件）：让后端指向外部目录（重启一次，之后只换文件）
AUTOTEST_FRONTEND_DIR=/path/to/dist sh -c './autotest'      # 或设置 runtime.frontend_dir
```

热更出来的页面仍会显示它的 `页面版本·提交`（构建时注入），所以"这版是哪个提交打的"可追溯；
如果这版要正式发布，再 `git tag` 标记即可（热更本身不需要新版本号）。

### 发版（打 tag）

```bash
sh scripts/release.sh 0.1.1        # 同步前后端版本号 → 提交 → 打 annotated tag v0.1.1
sh scripts/release.sh              # 不填版本号 = 自动 patch +1（0.1.0 → 0.1.1）
sh scripts/release.sh 0.2.0 --rebuild   # 顺带重建前端产物
```

版本号四处自动同步：`backend/pyproject.toml`、`backend/app/__init__.py`、`frontend/package.json`、
`frontend/package-lock.json`。发版前要求工作区干净；tag 是**本地**的（仓库无远程，
不会推送到任何地方）。打完后：

```bash
git tag -l                       # 看所有版本标签
git show v0.1.1                  # 看该版本改了什么
git reset --hard v0.1.0          # 回退到某个版本
git bundle create autotest.bundle --all   # 备份整个仓库（含所有 tag）
```

常用：

```bash
git log --oneline --graph        # 历史
git diff                         # 未提交的改动
git diff HEAD~1 --stat           # 上一次提交改了什么
git checkout <hash> -- <file>    # 把某文件恢复到某个版本
```

## 开发模式（改代码热更新）

```bash
sh scripts/dev.sh            # 后端 8787(--reload) + 前端 5173(Vite HMR)，Ctrl+C 一起退出
# Windows: powershell -ExecutionPolicy Bypass -File scripts\dev.ps1
```

开发时访问 http://127.0.0.1:5173 （Vite 代理 `/api`、`/ws` 到 8787）。

### 其他常用命令

```bash
sh scripts/start.sh --rebuild      # 改过前端代码后重新构建并启动
cd backend && uv run pytest -q     # 后端测试（无需真机）
cd backend && uv run python ../scripts/check_env.py   # 环境自检（adb/设备/端口/jar）
sh scripts/build.sh --pack         # 打包成单文件可执行程序（PyInstaller → backend/dist/autotest）
```

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

## 版本管理（本地 git，无远程）

仓库是**纯本地** git（`git remote -v` 为空），不联网、不推送。每次改动想留档时：

```bash
sh scripts/save.sh "改了镜像布局"     # 一条命令提交当前改动
sh scripts/save.sh --log             # 看最近提交（图形化）
```

git 会忽略：`backend/.venv`、`frontend/node_modules`、`frontend/dist`、`backend/dist`（打包产物）、
`~/.autotest/data` 之外的数据文件、`__pycache__` 等；**会**跟踪 `backend/vendor/scrcpy-server-v5.0.1.jar`
（734KB，保证离线可用）。仓库约 3MB。

常用：

```bash
git log --oneline --graph        # 历史
git diff                         # 未提交的改动
git diff HEAD~1 --stat           # 上一次提交改了什么
git checkout <hash> -- <file>    # 把某文件恢复到某个版本
git tag -l                       # 版本标签（当前 v0.1.1）
```

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
