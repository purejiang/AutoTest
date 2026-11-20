# 一键启动：后端(8787) + 前端(5173)，Ctrl+C 同时退出
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$BackendPort = if ($env:AUTOTEST_PORT) { $env:AUTOTEST_PORT } else { "8787" }

if (-not (Test-Path "backend\.venv")) {
    Write-Host "[dev] 首次运行，安装后端依赖…"
    Push-Location backend; uv sync --extra dev; Pop-Location
}
if (-not (Test-Path "frontend\node_modules")) {
    Write-Host "[dev] 安装前端依赖…"
    Push-Location frontend; npm install --no-audit --no-fund; Pop-Location
}

Write-Host "[dev] 后端 http://127.0.0.1:$BackendPort"
$backend = Start-Process -PassThru -NoNewWindow -WorkingDirectory "backend" `
    -FilePath "uv" -ArgumentList @("run", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", $BackendPort, "--reload")
Start-Sleep -Seconds 2
Write-Host "[dev] 前端 http://127.0.0.1:5173"
$env:AUTOTEST_BACKEND = "http://127.0.0.1:$BackendPort"
$frontend = Start-Process -PassThru -NoNewWindow -WorkingDirectory "frontend" -FilePath "npm" -ArgumentList @("run", "dev")

try {
    Wait-Process -Id $backend.Id, $frontend.Id
} finally {
    Write-Host "[dev] 停止服务…"
    Stop-Process -Id $backend.Id -ErrorAction SilentlyContinue
    Stop-Process -Id $frontend.Id -ErrorAction SilentlyContinue
}
