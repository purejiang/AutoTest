# Windows / Linux 部署

AutoTest 是**单机应用**：一个 FastAPI 进程同时提供 API、WebSocket 和 Vue 静态页面；
数据落 SQLite，不依赖 Docker / PostgreSQL / Redis。Android 设备必须通过 USB（或
`adb connect`）挂在这台机器上。

## 1. 先选部署形态

| 形态 | 适合 | 运行依赖 |
|---|---|---|
| 源码运行 | 开发机、长期可维护的 Linux 小主机 | Python 3.12（uv）、Node 20.19+/22 仅构建用、adb |
| PyInstaller 单文件 | 给不装 Python/Node 的 Windows/Linux 用户 | 仅 adb；前端已打进可执行文件 |
| 前端独立热更 | 打包后只想换界面 | 额外提供 `frontend/dist` 目录 |

> 打包产物**不能跨平台**：Windows 包在 Windows 上打，Linux 包在 Linux 上打。

## 2. 通用前置

- Android platform-tools（adb）：Windows `%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe`；
  Linux 用 Google 的 platform-tools（不要用发行版里过旧的 `adb`）。
- 手机开启「开发者选项 → USB 调试」，第一次连接时在手机上确认授权。
- `backend/vendor/scrcpy-server-v5.0.1.jar` 已随仓库提供；镜像/录制不需要额外安装 scrcpy。
- 浏览器建议 **Chrome / Edge**；镜像依赖 WebCodecs + H.264，旧 Safari/Firefox 可能不支持。
- 数据目录默认：
  - Windows：`%USERPROFILE%\.autotest\data`
  - Linux：`~/.autotest/data`

## 3. Windows

### 3.1 源码运行

1. 安装 Node.js **20.19+ / 22+**、Android platform-tools，并把 `adb` 加入 PATH
   （或在设置页填写 adb 绝对路径）。
2. 在项目根目录用 PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Open
```

`start.ps1` 会自动建 `backend\.venv`、构建 Vue，并启动 `http://127.0.0.1:8787`。

- 换端口：`-Port 9000`
- 局域网访问：`-BindHost 0.0.0.0`（先看第 6 节安全说明）
- 改前端后：`-Rebuild`

### 3.2 打包 exe

在 **Windows** 上执行（PowerShell，项目根目录）：

```powershell
cd frontend
npm ci
npm run build
cd ..\backend
uv sync --extra build
uv run pyinstaller --clean --noconfirm autotest.spec
```

产物：`backend\dist\autotest.exe`。运行：

```powershell
.\backend\dist\autotest.exe --data-dir C:\AutoTestData --host 127.0.0.1 --port 8787
```

`autotest.exe` 已内置 `frontend/dist`；仍需要系统里有 `adb`。
如果只想热更前端，打包后设：

```powershell
$env:AUTOTEST_FRONTEND_DIR = "C:\AutoTest\frontend-dist"
.\backend\dist\autotest.exe
```

### 3.3 作为 Windows 服务常驻

建议用 [NSSM](https://nssm.cc/) 或任务计划程序，并且**用登录用户账号运行**，
这样 adb 授权密钥、USB 设备和桌面会话都可见；不要用 SYSTEM 账号直接跑。

NSSM 示例（源码模式）：

```powershell
nssm install AutoTest "C:\AutoTest\backend\.venv\Scripts\python.exe" `
  "-m uvicorn app.main:app --host 127.0.0.1 --port 8787"
nssm set AutoTest AppDirectory "C:\AutoTest\backend"
nssm set AutoTest AppEnvironmentExtra AUTOTEST_DATA_DIR=C:\AutoTestData
nssm start AutoTest
```

打包模式把 `Application` 换成 `C:\AutoTest\autotest.exe`，参数用
`--data-dir C:\AutoTestData --host 127.0.0.1 --port 8787`。
需要在防火墙放行端口时，只放行可信网段。

## 4. Linux

### 4.1 USB 权限（必须）

先确认：

```bash
adb devices
# 应看到 <serial>	device；unauthorized 表示手机上还没点允许
```

如果 `no permissions`，安装 udev 规则（把 `idVendor` 换成手机厂商的 USB Vendor ID）：

```bash
sudo tee /etc/udev/rules.d/51-android.rules >/dev/null <<'EOF'
SUBSYSTEM=="usb", ATTR{idVendor}=="2717", MODE="0666", GROUP="plugdev"
SUBSYSTEM=="usb", ATTR{idVendor}=="18d1", MODE="0666", GROUP="plugdev"
EOF
sudo udevadm control --reload-rules
sudo udevadm trigger
sudo usermod -aG plugdev "$USER"   # 重新登录后生效
```

（小米 `2717`、Google `18d1` 是常见值；其他厂商查 `lsusb` 的 VID。）

### 4.2 源码运行

```bash
# 依赖：git、curl、adb、Node 20.19+/22；Python 由 uv 管理
sh scripts/setup.sh
sh scripts/start.sh --host 0.0.0.0 --port 8787
```

只本机访问时不要加 `--host 0.0.0.0`。

### 4.3 打包

在目标 Linux 发行版上构建（PyInstaller 不跨平台；尽量在较老的 glibc 基础上构建）：

```bash
cd frontend && npm ci && npm run build && cd ..
sh scripts/build.sh --pack
# 产物：backend/dist/autotest
./backend/dist/autotest --data-dir /var/lib/autotest --host 127.0.0.1 --port 8787
```

打包产物内置前端；系统仍需要 `adb`。

### 4.4 systemd

```ini
# /etc/systemd/system/autotest.service
[Unit]
Description=AutoTest Android UI automation
After=network.target

[Service]
Type=simple
User=autotest
Group=plugdev
WorkingDirectory=/opt/autotest/backend
Environment=AUTOTEST_DATA_DIR=/var/lib/autotest
Environment=AUTOTEST_HOST=127.0.0.1
Environment=AUTOTEST_PORT=8787
ExecStart=/opt/autotest/backend/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8787
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

打包模式把 `ExecStart` 换成：

```ini
ExecStart=/opt/autotest/autotest --data-dir /var/lib/autotest --host 127.0.0.1 --port 8787
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now autotest
sudo journalctl -u autotest -f
```

数据目录归 `autotest` 用户所有；服务账号必须能看到 USB 设备（`plugdev`）。
没有桌面登录的 Linux 服务器，第一次 adb 授权仍要在手机上点允许。

## 5. 反向代理与 WebSocket

如果要在局域网用域名/HTTPS，用 Nginx/Caddy 反代 `127.0.0.1:8787`，并注意：

- `/api`、`/artifacts` 正常 HTTP；
- `/ws` 必须转发 Upgrade/Connection，不能缓冲；
- 超时时间要足够长（镜像/运行事件是长连接）。

Nginx 片段：

```nginx
location / {
    proxy_pass http://127.0.0.1:8787;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 3600s;
}
```

## 6. 安全（重要）

v0.2 的 `security.token` **只用于镜像 WebSocket**；REST API 没有统一鉴权。
因此：

- 个人使用：只绑定 `127.0.0.1`，通过本机浏览器或 SSH 隧道访问；
- 局域网：只放行可信网段，并在反向代理层加 Basic Auth / mTLS / VPN；
- **不要把 8787 直接暴露到公网**。设备控制接口能操作 USB 连接的手机。

## 7. 数据、备份与升级

- 数据目录包含 `autotest.db`（SQLite WAL）、`artifacts/`、`reports/`、日志。
- 备份时先停服务，再复制整个数据目录；至少要包含 `autotest.db`、`-wal`、`-shm`。
- v0.2 不迁移 v0.1.x 的旧库；启动会明确报不兼容，不会自动删除。
  升级前备份并移走旧 `autotest.db`，再启动。
- 换版本：替换代码/可执行文件，保留 `AUTOTEST_DATA_DIR`；前端 dist 可单独热更。

## 8. 部署后自检

```bash
curl http://127.0.0.1:8787/api/health
curl -I http://127.0.0.1:8787/
adb devices
```

浏览器打开后确认：设备页能看到真机、镜像能起流、脚本能运行、运行详情能收到 WS 事件。
