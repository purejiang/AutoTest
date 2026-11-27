# AutoTest · Android 自动化测试工具

本地单机的 Android UI 自动化测试平台：**连接手机 → 脚本编排 / 真实操作录制 → 顺序执行 → 测试报告**。
后端 Python（adb + uiautomator2 + scrcpy），前端 Vue 3（v0.2 起），同一套代码跑 macOS / Windows / Linux。

## 功能

| 能力 | 说明 |
|---|---|
| 设备控制 | 点击 / 长按 / 滑动 / 拖拽 / 输入 / 系统键（返回·桌面·最近任务·音量·电源）/ 启停清应用 / shell / 截图 / 录屏 / logcat |
| 实时镜像 | scrcpy 服务端 + WebCodecs 低延迟解码，鼠标直接在镜像上操作设备（归一化坐标，后端反算设备像素） |
| 镜像触控特效 | 工具栏三个独立开关（记住选择）：点击特效（触点/长按环）、波纹、滑动特效（轨迹/跟随点） |
| 镜像画质预设 | 设置页三档画质（轻量 70%/6M、标准 85%/10M、高清 100%/16M，按设备长边比例缩放 + 长边上限 + 码率）+ 自定义（缩放/上限/码率/裁剪的详细参数只在选自定义时展开，预设卡片上直接显示取值）；帧率是独立项（下拉：不限(0)/30/45/60/90/120，默认不限同 scrcpy；配置里的非列表值仍显示为「当前」项），任何预设都可单独限帧；保存后停止并重新启动镜像生效 |
| 步骤脚本 | JSON 数据文件，顺序执行；每个节点独立配置执行条件、定位方式、超时、停顿、重试、失败策略、节点名/注释、重跑安全性；脚本设置里可配「全节点默认执行前/后停顿」，节点停顿填 0 即继承默认、填非 0 即该节点自定义（录制转换生成的脚本不继承默认，节奏只由录制还原的间隔决定）；设置页「执行 → 运行节奏」可配「新建脚本默认停顿」，仅作为新建脚本的初值，不改已有脚本 |
| 脚本变量 | 脚本设置里声明 `variables` 默认值，步骤里的目标/文本/包名/shell/断言/screenshot 名称等字符串字段支持 `{{var}}` 插值；编辑器输入框右侧「{{ }}」按钮可从现有变量里选并插入到光标处；控制台「运行参数」弹窗按脚本传本次运行的值（记住上次覆盖），运行记录展示实际生效变量 |
| 元素拾取 | 脚本节点的「从截图选择」：在设备截图点选位置，按层级选元素或直接用坐标回填 target（index 按文档顺序自动计算，与录制/回放一致）；手动控制「层级树」在截屏上叠加层级框，悬停看元素信息、树图联动、一键复制选择器 |
| 12 类节点 | click / long_click / swipe / drag / input / key / wait / app / shell / assert / sub_script / screenshot |
| 脚本录制 | **镜像网页操作录制（主）** + 真机操作录制（辅助，getevent）；支持暂停、计时、事件数；录制草稿可一键转脚本/重新生成；脚本可作为节点嵌入其他脚本 |
| 视频录制 | **浏览器端录制镜像画面**（可叠加点击/滑动特效），支持暂停/继续、计时、停止后预览/拖动/下载；入口在控制台「手动控制 → 视频」；设备端 screenrecord 只保留后端 API（控制台入口已移除） |
| 持久化 | 脚本是 `~/.autotest/scripts/*.json`（可直接进 git / 拷贝 / 手改），配置是 `~/.autotest/config.json`；运行记录、步骤结果、事件流、产物、报告存 SQLite（WAL），带 CRUD |
| 报告 | 一键生成 zip（HTML 汇总 + JUnit XML + 失败截图），支持逐节点耗时与失败原因 |
| 调试 | 断点、单步、从指定节点重跑；暂停后解锁手动控制（干预留痕） |
| 治理 | 30 天保留策略、清理统计、报告体积上限（不含视频） |

## 快速开始

### 前置

- Python **3.12**（用 [uv](https://docs.astral.sh/uv/) 管理，无需系统 Python 升级）
- Node.js **20.19+ / 22+**（Vite 8 要求；npm）
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

浏览器打开 **http://127.0.0.1:8787** ，终端窗口关掉即停止。也可以另开终端用停止命令：

```bash
# macOS / Linux
sh scripts/stop.sh                 # 停止 8787 上的 AutoTest
sh scripts/stop.sh --port 9000     # 指定端口
sh scripts/stop.sh --dev           # 同时停开发前端 5173

# Windows (PowerShell)
powershell -ExecutionPolicy Bypass -File scripts\stop.ps1
powershell -ExecutionPolicy Bypass -File scripts\stop.ps1 -Dev
```

macOS 还可以双击根目录的「停止AutoTest.command」。v0.2 默认构建 Vue 3 主前端；需要回滚时加 `--react`。

首次运行脚本会自动完成：装 uv（若缺）→ Python 3.12 + 后端依赖 → 前端依赖 + 构建 → 下载 scrcpy-server → 启动。
也可以先手动跑一次 `sh scripts/setup.sh`（Windows `scripts\setup.ps1`）做完整安装 + 环境自检。

生产/长期部署（macOS / Windows / Linux、Windows 服务、Linux systemd、udev、PyInstaller 单文件、反向代理与安全边界）
见 [docs/deployment.md](docs/deployment.md)。

### 前端技术栈与回滚（v0.2）

| 目录 | 说明 |
|---|---|
| `frontend/` | **Vue 3 主前端**：Vue 3.5 + Vite + TypeScript + Vue Router 4 + Pinia + Element Plus + Axios + VueUse + @tanstack/vue-query + CodeMirror 6 |
| `frontend-react/` | v0.1.3 React + Ant Design **冻结备份**，只用于回滚/对照，不再继续开发 |
| `frontend/dist/` | 后端唯一静态目录；Vue 构建默认输出到这里 |

回滚到 React 前端（后端无需修改，构建脚本会把 React 产物同步到 `frontend/dist`）：

```bash
sh scripts/dev.sh --react       # 开发模式：frontend-react/ + 后端 8787
sh scripts/build.sh --react     # 生产构建：React 产物同步到 frontend/dist
sh scripts/start.sh --react     # 生产启动（需要时自动构建并同步）
# 也可用环境变量：AUTOTEST_FRONTEND=react sh scripts/start.sh
```

v0.2 脚本数据不兼容旧 `when` / 旧 `source`：前端按 `source = manual | mirror | physical | imported`
和新的条件结构渲染，不写旧数据兜底分支。

v0.2 也不迁移旧数据库。升级前请先备份，再移走或删除
`~/.autotest/data/autotest.db`（或整个 `~/.autotest/data`）后重新启动；启动时的 schema guard
会明确提示旧库不兼容，不会自动删除数据。

### 版本管理（本地 git + GitHub 远端）

仓库原本是**纯本地** git；现在挂了 GitHub 远端 `origin`（`https://github.com/purejiang/AutoTest`，公开），
用来发布源码快照：日常改动只管本地提交/打 tag，确认要发布时再 `git push origin main --tags`。
每次改动想留档时：

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

版本号同步范围：`backend/pyproject.toml`、`backend/app/__init__.py`、`frontend/package.json`、
`frontend/package-lock.json`。**release 只同步 `frontend/`**；`frontend-react/` 是冻结回滚备份，
不参与发版。发版前要求工作区干净；tag 先打在**本地**，推送后 GitHub 会自动给出该 tag 的源码 zip
（`https://github.com/purejiang/AutoTest/archive/refs/tags/v0.2.6.zip`），**不需要建 Release**。打完后：

```bash
git push origin main             # 推源码
git push origin --tags           # 推版本 tag（GitHub 自动生成 Source code zip）
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
sh scripts/dev.sh            # Vue 主前端 + 后端 8787(--reload)，Ctrl+C 一起退出
sh scripts/dev.sh --react    # 回滚/对照：frontend-react/ + 后端
sh scripts/start.sh --reload # 单端口 8787 热更新后端（.py 改动自动重启；前端仍用 dist）
sh scripts/dev-reload.sh     # 一键重启 8787 + 热更新（先停旧进程，挂了自动拉起；可加 --watch-frontend）
# Windows: powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 [-React]
#          powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Reload
```

开发时访问 http://127.0.0.1:5173 （Vite 代理 `/api`、`/ws` 到 8787）。
`--reload` 只监听后端 Python 代码，重启会中断正在执行的运行任务，**跑批/演示时不要开**。

### 其他常用命令

```bash
sh scripts/start.sh --rebuild      # 改过前端代码后重新构建并启动
sh scripts/build.sh                # 只构建 Vue 前端到 frontend/dist
cd frontend && npm run lint        # Vue/TS ESLint
cd frontend && npm run typecheck   # 类型检查
cd frontend && npm run test        # Vitest 单测
cd frontend && npm run build       # 类型检查 + 生产构建
cd backend && uv run pytest -q     # 后端测试（无需真机）
cd backend && uv run python ../scripts/check_env.py   # 环境自检（adb/设备/端口/jar）
sh scripts/build.sh --pack         # 打包成单文件可执行程序（PyInstaller → backend/dist/autotest）
sh scripts/stop.sh                 # 停止 AutoTest（--port / --dev / --dry-run）
```

### 使用流程

1. **控制台**：确认设备在线 → 启动镜像 → 直接在镜像上点按操作；右侧「手动控制」分视频 / 镜像 / 文本 / 应用 / 工具；「工具」里以侧边抽屉打开层级树、logcat、网络抓包、设备信息等；「自动控制」选脚本并实时看运行节点；点「运行」会先弹运行参数（脚本有变量时），也可用行内「参数」固定打开。
2. **脚本页**：脚本库与运行记录合并为两个 tab；新建脚本（手动 / 从镜像录制 / 从真机录制），节点支持拖拽排序和 `Alt+↑/↓`，点「运行」会跳到控制台自动控制并启动；节点的点击目标旁有「从截图选择」，可直接在设备截图上拾取元素或坐标；可插值输入框右侧的「{{ }}」按钮能直接选脚本变量插入。
3. **录制页**：只收录操作录制草稿（镜像 / 真机），录制详情通过 WS 实时追加事件；视频录制不落库，刷新即丢失。
4. **设置页**：通用（主题 / 语言）/ 工具链 / 执行（镜像画质 + 运行节奏 + 录制采集 + 安全）/ 存储 / 关于；主题支持浅色、深色、跟随系统。
5. **报告**：运行结束一键生成 zip，含 JUnit XML 与失败截图。
6. **logcat / 网络抓包**：控制台手动工具可开始/停止 logcat 采集并保存产物；网络抓包只记录域名（host/port/scheme），不含 Header 与正文，需要设备系统代理，当前同时只支持一台设备。网络抓包在设置里三选一：关闭 / 仅手动 / 跟随运行。
7. **失败证据**：节点失败时自动保存 logcat 尾部、节点开始后的域名记录，并关联到该节点的 step_result；需要在设置里开启对应采集。

## 已验证（真机 Xiaomi 23127PN0CC / Android 16 / 1200×2670）

| 能力 | 状态 |
|---|---|
| 设备发现·信息·层级·截图·shell·logcat·应用列表 | ✅ 实测 |
| logcat 采集（手动开始/停止 + 运行自动 attach） | 🧪 单元测试 |
| 网络域名抓包（只记 host/port，不含正文） | 🧪 单元测试 |
| 失败节点证据快照（logcat 尾部 + 时间窗域名 + 截图） | 🧪 单元测试 |
| 脚本执行（app/wait/click/assert/swipe/shell/screenshot/key） | ✅ 10 步 succeeded |
| 失败重试 · 失败截图 · on_fail abort/continue/goto | ✅ 实测 |
| 暂停 = 干预模式（暂停期手动控制 + `manual_intervention` 留痕）· 预检 · 热恢复 | ✅ 实测 |
| scrcpy 实时镜像（718×1600 / avc1.64002A / 首帧 IDR+SPS+PPS） | ✅ 后端 WS 收帧实测 |
| 镜像页操作直连 scrcpy control 通道（**点击端到端 4~40ms**） | ✅ 真机实测，坐标 3/3 落点正确 |
| 镜像录制 → 转脚本 → 回放 | ✅ 实测 succeeded |
| 真机录制（触摸设备探测 + 事件解析 + 标定字段） | ✅ 探测实测（标定精度待回归） |
| 设备 screenrecord API（mp4 可播放，控制台入口已移除）· 报告 zip（HTML+JUnit+截图）· 产物直链 | ✅ 实测 |
| 运行变量插值（app 包名 `{{package}}` + 请求覆盖） | 🧪 单元测试 / FakeDriver |
| 脚本变量选择器与运行参数弹窗 | 🧪 前端 typecheck/build + 浏览器端到端 |
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
frontend/         Vue 3.5 + Vite + TS + Element Plus + Pinia + Vue Query + CodeMirror 6
frontend-react/   v0.1.3 React + AntD 冻结备份（回滚用，不再开发）
docs/contracts.md 唯一契约（错误码/DB/脚本格式/驱动接口/WS/端点/配置）
samples/          示例脚本
scripts/          setup / dev / build / start / release / 环境自检 / scrcpy 下载
                  前端实现默认 Vue；--react 切换 frontend-react（回滚）
```

## 关键设计（详见 `docs/contracts.md`）

- **分层**：tools(adb/u2/scrcpy) → engine(顺序执行) → 数据/逻辑 → API → Web。
- **等待全部自轮询**（350ms，可被暂停/停止打断），禁用 uiautomator2 阻塞等待 API，保证暂停响应 ≤5s。
- **暂停 = 干预模式**：暂停后解锁手动控制，手动操作全部留痕（`manual_intervention`），恢复前做文字化预检。
- **重跑安全性**：节点标记 `rerun_safety ∈ {safe,destructive,stateful}`，破坏性节点禁止"重跑当前节点"。
- **v0.2 执行条件**：节点 `when = {mode, timeout_ms, poll_interval_ms, condition}`，`condition` 支持 group(and/or) / not / target / app / time；`when=null` 默认关闭，条件不满足按 require=失败、optional=跳过处理。
- **v0.2 脚本来源**：`manual | mirror | physical | imported`，录制生成的脚本带 `origin_recording_id`；前端不保留旧 `recorded` 兜底。
- **镜像**：scrcpy-server 版本与 SHA256 锁定；`--no-audio`；unique localabstract；背压丢帧到下一 IDR 并通知前端 flush；旋转/参数变化重配解码器。
- **契约冻结**：`docs/contracts.md` 是唯一事实来源，改动需先改文档再改代码。

## 配置（三层：默认 < 配置文件 < 环境变量，只存文件）

- 配置文件：`~/.autotest/config.json`（或 `AUTOTEST_CONFIG` 指定），嵌套 JSON，按点号读写，例如
  `{"engine": {"default_timeout_ms": 5000}, "scrcpy": {"max_size": 1200}}`
- 设置页保存 = 原子写这个文件并立即重载，**不写数据库**；手改文件后可点「重新载入配置文件」或重启
- 环境变量层：`AUTOTEST_DATA_DIR` / `AUTOTEST_ARTIFACTS_DIR` / `AUTOTEST_ADB` / `AUTOTEST_HOST` /
  `AUTOTEST_PORT` / `AUTOTEST_TOKEN` / `AUTOTEST_SCRCPY_JAR`，优先级最高
- **启动项**（数据目录、产物目录、前端目录、host、port）：打开数据库前就要确定，运行中改了必须重启；
  设置页只读，改配置文件/环境变量后重启
- 数据库只存运行数据：运行记录、步骤结果、事件、产物索引、报告；配置是文件
- 数据目录：`~/.autotest/data`（`runtime.data_dir` 可改），放运行记录、产物、报告和日志
- 脚本目录：`~/.autotest/scripts/*.json`（`runtime.scripts_dir` / `AUTOTEST_SCRIPTS_DIR` 可改），一个脚本一个文件

## 版本管理（本地 git + GitHub 远端）

远端 `origin` = `https://github.com/purejiang/AutoTest`（公开源码镜像）；本地提交/打 tag 不变，
需要发布时 `git push origin main --tags`。每次改动想留档时：

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
git tag -l                       # 版本标签（当前 v0.2.6）
```

## 开发

```bash
cd backend && uv run pytest -q                 # 后端测试（无需真机）
cd backend && uv run uvicorn app.main:app --reload --port 8787
cd frontend && npm run dev                      # Vue 前端 dev（代理到 8787）
cd frontend && npm run lint                     # ESLint / Vue 规则
cd frontend && npm run typecheck                # 类型检查
cd frontend && npm run test                     # Vitest 单测（无需后端）
cd frontend && npm run types:check               # 前端类型与 docs/openapi.json 一致
cd frontend && npm run build                    # 类型检查 + 构建到 frontend/dist
# 回滚：cd frontend-react && npm run dev / npm run build
```

无真机也能开发/联调：设备注册表会把 serial 以 `fake` 开头的设备路由到内存 `FakeDriver`（假层级树 + 动作记录），
前端与引擎都可用它跑通全链路。默认设备列表不会显示 fake 设备（避免掩盖“未连接/adb 不可用”）；
需要离线演示时用 `AUTOTEST_FAKE_DEVICE=1 sh scripts/start.sh` 启动，列表会附带 `fake-1`。

## 常见问题

- **npm 报 EPERM（`~/.npm` 缓存被 root 占用）**：`sudo chown -R $(id -u):$(id -g) ~/.npm`，或临时用 `npm_config_cache=/tmp/autotest-npm-cache npm install`。
- **国内下载 scrcpy-server 失败/很慢（GitHub 连不上）**：`scripts/fetch_scrcpy.py` 已内置镜像回退——官方 Release 失败后自动尝试
  `gh-proxy.com` → `ghfast.top` → `ghproxy.net`，每份下载都必须与官方 SHA256 一致才落盘（镜像只能当通道，换不了内容）。
  也可以自己用 `AUTOTEST_SCRCPY_URL=https://gh-proxy.com/https://github.com/... python scripts/fetch_scrcpy.py` 指定单一地址。
  手工下载后放到 `backend/vendor/scrcpy-server-v5.0.1.jar`，再跑 `--check` 校验即可。
- **adb 找不到**：设置页填 adb 绝对路径，或把 platform-tools 加进 PATH。
- **设备 offline/unauthorized**：重新插拔、手机上确认授权、`adb kill-server && adb start-server`。
- **镜像黑屏/报错**：确认 scrcpy-server jar 已下载（`sh scripts/setup.sh`），在设置页调小 `max_size`(如 800) 与码率。
- **操作不如 scrcpy 跟手**：先确认设置页「帧率」是 0（不限，这也是 scrcpy 的默认值）——限成 30 会多一帧采样等待、滚动不顺，是观感差距最大的因素。若画面开始一顿一顿，说明电脑解码跟不上，按 scrcpy 的官方建议降分辨率/码率（选「轻量」70%）而不是限帧；镜像状态行的「设备出帧 Nfps」（后端从设备读到几帧）vs 左上角渲染 fps（画布实际画了几帧）可以直接看出瓶颈：设备出帧明显高于渲染＝浏览器端跟不上。对比手感时关掉「特效」（本地轨迹零延迟，会放大设备响应的滞后感）。
- **换了 Chrome 就流畅了**：Safari 的 WebCodecs 解码路径明显更慢（旧版甚至没有 `VideoDecoder`），镜像跟不上时优先换
  Chrome/Edge；此时“降分辨率/限帧”往往没用，因为瓶颈在浏览器实现而不是参数。
- **浏览器端解码/绘制跟不上（设备出帧高、渲染 fps 低）**：先看状态行两个新指标——「解码队列 N」（>2 表示
  WebCodecs 排队，越大越滞后）和「绘制 X ms」（单帧 drawImage 耗时）。要留证据时：
  浏览器控制台每 5 秒会打一行 `[mirror-diag]`（出帧/渲染/绘制/队列/丢帧），执行 `__autotestMirrorDiag()`
  可一次导出最近 120 行；后端日志每 10 秒一行 `mirror perf:`（`fps` `dropped` `subscribers` `queues`
  `stream` `rotation` `max_fps`），`queues` 持续大于 0 说明浏览器消费不过来。
  这些样本还会落盘到 `<data_dir>/diag/mirror-YYYYMMDD.jsonl`（浏览器每 5s 一条 `source=client`，
  后端每 10s 一条 `source=server`），事后直接分析：

  ```bash
  python scripts/analyze_mirror_diag.py            # 默认只看最新镜像会话（换参数/换浏览器=新会话）
  python scripts/analyze_mirror_diag.py --list     # 有哪些文件（体积/行数）
  python scripts/analyze_mirror_diag.py --session ID   # 指定会话
  python scripts/analyze_mirror_diag.py --all      # 所有会话
  ```**队列涨、绘制便宜**→卡在解码：按官方
  建议降分辨率（「轻量」70%，还可再降到 50%），或把帧率限到本机屏幕刷新率（60Hz 屏设 0 会按设备 120Hz 推流，
  约一半解码白费，设置页会给出提示）。**绘制 ms 高**（>8ms）→卡在绘制/GPU 上传：降低缩放或减少画布 DPR。
- **浏览器不支持 WebCodecs**（Firefox 旧版/Safari 旧版）：镜像页会提示；此时用**真机录制**模式（手机真实操作，通过 getevent 采集）。
- **录制没有出现节点**：检查录制的设备是否就是操作的那台；真机录制需要先通过触摸设备确认与 2 点标定。
- **运行时提示 409 DEVICE_BUSY**：设备被运行/录制占用；手动控制（含 shell）在运行中会被拒绝，暂停运行后即可操作（并会留痕）。
- **断言失败但页面看着是对的**：优先用「前台包名」或 `text` 精确匹配；`text_contains` 容易命中包含关系的其它文案；
  系统设置页可能跨进程（包名变化），详见 `docs/real-device-notes.md` 的脚本编写最佳实践。
