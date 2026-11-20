# 首次安装（Windows PowerShell）
$ErrorActionPreference = "Continue"
Set-Location (Join-Path $PSScriptRoot "..")

Write-Host "==> 1/5 检查 uv"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "    安装 uv…"
    irm https://astral.sh/uv/install.ps1 | iex
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

Write-Host "==> 2/5 安装 Python 3.12 与后端依赖"
Push-Location backend
uv python install 3.12
uv sync --extra dev
Pop-Location

Write-Host "==> 3/5 安装前端依赖"
Push-Location frontend
npm install --no-audit --no-fund
Pop-Location

Write-Host "==> 4/5 下载 scrcpy-server"
Push-Location backend
if (-not (Test-Path "vendor\scrcpy-server-v5.0.1.jar")) {
    uv run python ..\scripts\fetch_scrcpy.py
}
Pop-Location

Write-Host "==> 5/5 环境自检"
Push-Location backend
uv run python ..\scripts\check_env.py
Pop-Location

Write-Host ""
Write-Host "完成。启动：powershell -File scripts\dev.ps1"
