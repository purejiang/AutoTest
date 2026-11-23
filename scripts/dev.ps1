# 开发模式：后端(8787) + 前端(5173)，Ctrl+C 同时退出
# 默认 Vue 主前端 frontend/；加 -React 回滚到冻结的 frontend-react/。
param(
    [switch]$React,
    [switch]$Vue
)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$FrontendImpl = if ($env:AUTOTEST_FRONTEND) { $env:AUTOTEST_FRONTEND } else { "vue" }
if ($React) { $FrontendImpl = "react" }
if ($Vue) { $FrontendImpl = "vue" }
switch ($FrontendImpl) {
    "vue" { $FrontendDir = "frontend" }
    "react" { $FrontendDir = "frontend-react" }
    default { Write-Host "[dev] AUTOTEST_FRONTEND 只能是 vue 或 react（收到：$FrontendImpl）"; exit 2 }
}

$BackendPort = if ($env:AUTOTEST_PORT) { $env:AUTOTEST_PORT } else { "8787" }

if (-not (Test-Path "backend\.venv")) {
    Write-Host "[dev] 首次运行，安装后端依赖…"
    Push-Location backend; uv sync --extra dev; Pop-Location
}
if (-not (Test-Path "$FrontendDir\node_modules")) {
    Write-Host "[dev] 安装前端依赖（$FrontendDir）…"
    Push-Location $FrontendDir; npm install --no-audit --no-fund; Pop-Location
}

Write-Host "[dev] 前端实现：$FrontendImpl（$FrontendDir）"
if ($FrontendImpl -eq "react") {
    Write-Host "[dev] 注意：frontend-react/ 是 v0.1.3 的回滚备份，不再继续开发。"
}
Write-Host "[dev] 后端 http://127.0.0.1:$BackendPort"
$backend = Start-Process -PassThru -NoNewWindow -WorkingDirectory "backend" `
    -FilePath "uv" -ArgumentList @("run", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", $BackendPort, "--reload")
Start-Sleep -Seconds 2
Write-Host "[dev] 前端 http://127.0.0.1:5173"
$env:AUTOTEST_BACKEND = "http://127.0.0.1:$BackendPort"
$frontend = Start-Process -PassThru -NoNewWindow -WorkingDirectory $FrontendDir -FilePath "npm" -ArgumentList @("run", "dev")

try {
    Wait-Process -Id $backend.Id, $frontend.Id
} finally {
    Write-Host "[dev] 停止服务…"
    Stop-Process -Id $backend.Id -ErrorAction SilentlyContinue
    Stop-Process -Id $frontend.Id -ErrorAction SilentlyContinue
}
