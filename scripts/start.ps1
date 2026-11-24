# AutoTest 一键启动（Windows，生产模式：后端同时托管前端，只开一个进程/端口）
#
#   powershell -ExecutionPolicy Bypass -File scripts\start.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Open -Port 9000 -Rebuild
#   powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -React   # 回滚 React 前端
param(
    [int]$Port = 8787,
    [string]$BindHost = "127.0.0.1",
    [switch]$Open,
    [switch]$Rebuild,
    [switch]$React,
    [switch]$Vue
)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$Root = (Get-Location).Path

function Say([string]$m)  { Write-Host "[start] $m" -ForegroundColor Cyan }
function Warn([string]$m) { Write-Host "[start] $m" -ForegroundColor Yellow }
function Die([string]$m)  { Write-Host "[start] $m" -ForegroundColor Red; exit 1 }

$FrontendImpl = if ($env:AUTOTEST_FRONTEND) { $env:AUTOTEST_FRONTEND } else { "vue" }
if ($React) { $FrontendImpl = "react" }
if ($Vue) { $FrontendImpl = "vue" }
switch ($FrontendImpl) {
    "vue" { $FrontendDir = "frontend" }
    "react" { $FrontendDir = "frontend-react" }
    default { Die "AUTOTEST_FRONTEND 只能是 vue 或 react（收到：$FrontendImpl）" }
}

# 1. uv
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    $uvPath = Join-Path $env:USERPROFILE ".local\bin"
    if (Test-Path (Join-Path $uvPath "uv.exe")) {
        $env:Path = "$uvPath;$env:Path"
    } else {
        Say "未安装 uv，正在自动安装…"
        irm https://astral.sh/uv/install.ps1 | iex
        $env:Path = "$uvPath;$env:Path"
        if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { Die "uv 安装失败，请手动安装后重试" }
    }
}

# 2. 后端依赖
if (-not (Test-Path "backend\.venv\Scripts\python.exe")) {
    Say "首次运行：安装 Python 3.12 与后端依赖（约 1 分钟）…"
    Push-Location backend; uv python install 3.12; uv sync; Pop-Location
}

# 3. 前端构建（后端静态目录固定为 frontend/dist）
$builtImpl = ""
if (Test-Path "frontend\dist\.impl") { $builtImpl = (Get-Content "frontend\dist\.impl" -Raw).Trim() }
$needBuild = (-not (Test-Path "frontend\dist\index.html")) -or $Rebuild -or ($builtImpl -eq "") -or ($builtImpl -ne "" -and $builtImpl -ne $FrontendImpl)
if ($needBuild) {
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        if (-not (Test-Path "$FrontendDir\node_modules")) {
            Say "安装前端依赖（$FrontendDir，首次约 1-2 分钟）…"
            Push-Location $FrontendDir; npm install --no-audit --no-fund; Pop-Location
        }
        Say "构建前端（$FrontendImpl / $FrontendDir）…"
        Push-Location $FrontendDir; npm run build; Pop-Location
        if ($FrontendImpl -eq "react") {
            Say "同步 React 产物到 frontend\dist …"
            if (Test-Path "frontend\dist") { Remove-Item "frontend\dist" -Recurse -Force }
            New-Item -ItemType Directory -Path "frontend\dist" | Out-Null
            Copy-Item "frontend-react\dist\*" "frontend\dist" -Recurse -Force
        }
        Set-Content -Path "frontend\dist\.impl" -Value $FrontendImpl -NoNewline
    } else {
        Warn "未找到 npm/Node.js：跳过前端构建，仅 API 可用（/api、/docs）"
    }
}

# 4. scrcpy-server
if (-not (Test-Path "backend\vendor\scrcpy-server-v5.0.1.jar")) {
    Say "下载 scrcpy-server（镜像需要）…"
    Push-Location backend
    try { uv run python ..\scripts\fetch_scrcpy.py } catch { Warn "scrcpy-server 下载失败：镜像不可用" }
    Pop-Location
}

# 5. 端口检查
$inUse = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($inUse) { Die "端口 $Port 已被占用，换个端口：-Port 8788" }

$url = "http://${BindHost}:${Port}"
Say "启动 AutoTest -> $url （API 文档 $url/docs，Ctrl+C 退出）"
Say "另开终端停止：powershell -ExecutionPolicy Bypass -File scripts\stop.ps1 $(if ($Port -ne 8787) { "-Port $Port" })"
if ($Open) { Start-Process $url }

Push-Location backend
# 优先用项目自带 venv，避免 uv 全局缓存权限异常导致启动失败。
if (Test-Path ".venv\Scripts\python.exe") {
    & ".venv\Scripts\python.exe" -m uvicorn app.main:app --host $BindHost --port $Port
} else {
    uv run uvicorn app.main:app --host $BindHost --port $Port
}
Pop-Location
