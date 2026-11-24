# 停止 AutoTest（生产或开发模式的后端；可选同时停 Vite）。
#
#   powershell -ExecutionPolicy Bypass -File scripts\stop.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\stop.ps1 -Port 9000
#   powershell -ExecutionPolicy Bypass -File scripts\stop.ps1 -Dev
#   powershell -ExecutionPolicy Bypass -File scripts\stop.ps1 -DryRun
#
# 按端口找监听进程，并校验它确实是本项目的 AutoTest/uvicorn/Vite 进程，
# 避免误杀占用同一端口的其它服务。也可以继续用 Ctrl+C 停前台进程。
param(
    [int]$Port = 0,
    [switch]$Dev,
    [switch]$DryRun,
    [switch]$Force
)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$Root = (Get-Location).Path

if ($Port -le 0) {
    $envPort = 0
    if ($env:AUTOTEST_PORT -and [int]::TryParse($env:AUTOTEST_PORT, [ref]$envPort) -and $envPort -gt 0) {
        $Port = $envPort
    } else {
        $Port = 8787
    }
}

function Say([string]$m)  { Write-Host "[stop] $m" -ForegroundColor Cyan }
function Warn([string]$m) { Write-Host "[stop] $m" -ForegroundColor Yellow }
function Die([string]$m)  { Write-Host "[stop] $m" -ForegroundColor Red; exit 1 }

function Get-ListenerProcessIds([int]$listenPort) {
    $connections = Get-NetTCPConnection -LocalPort $listenPort -State Listen -ErrorAction SilentlyContinue
    if (-not $connections) { return @() }
    return @($connections | Select-Object -ExpandProperty OwningProcess -Unique)
}

function Get-ProcessInfo([int]$procId) {
    return Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction SilentlyContinue
}

function Test-AutoTestProcess([int]$procId, [string]$kind) {
    $info = Get-ProcessInfo $procId
    if (-not $info) { return $false }
    $commandLine = [string]$info.CommandLine
    if ($commandLine -match "app\.main:app|autotest") { return $true }
    if ($kind -eq "vite" -and $commandLine -match "vite") { return $true }
    return $false
}

function Add-Target([int]$procId, [System.Collections.Generic.List[int]]$targets) {
    if ($procId -gt 0 -and -not $targets.Contains($procId)) { $targets.Add($procId) }
}

function Collect-Port([int]$listenPort, [string]$kind, [string]$label, [System.Collections.Generic.List[int]]$targets) {
    $pids = @(Get-ListenerProcessIds $listenPort)
    if ($pids.Count -eq 0) {
        Say "端口 $listenPort 上没有运行中的$label"
        return
    }
    foreach ($procId in $pids) {
        if (Test-AutoTestProcess $procId $kind) {
            Add-Target $procId $targets
            # uvicorn --reload / npm 包装进程：把已识别的父进程也纳入停止范围
            $info = Get-ProcessInfo $procId
            $parentId = [int]($info.ParentProcessId)
            if ($parentId -gt 0 -and (Test-AutoTestProcess $parentId $kind)) {
                Add-Target $parentId $targets
            }
        } elseif ($Force) {
            Warn "PID $procId 不是 AutoTest 进程，-Force 仍将停止"
            Add-Target $procId $targets
        } else {
            $cmd = (Get-ProcessInfo $procId).CommandLine
            Warn "跳过 PID $procId（不是本项目的 AutoTest/Vite 进程）：$cmd"
        }
    }
}

$targets = [System.Collections.Generic.List[int]]::new()
Collect-Port $Port "backend" " AutoTest 服务" $targets
if ($Dev) {
    Collect-Port 5173 "vite" "开发前端（Vite）" $targets
}

if ($targets.Count -eq 0) {
    $suffix = if ($Dev) { " / 5173" } else { "" }
    Say "没有找到可停止的 AutoTest 进程（端口 $Port$suffix）"
    exit 0
}

if ($DryRun) {
    Say ("dry-run：将停止 PID: " + ($targets -join " "))
    exit 0
}

Say ("停止 PID: " + ($targets -join " ") + " …")
foreach ($procId in $targets) {
    Stop-Process -Id $procId -ErrorAction SilentlyContinue
}

# 最多等 5s，仍未退出则强制结束
$deadline = (Get-Date).AddSeconds(5)
while ((Get-Date) -lt $deadline) {
    $alive = @($targets | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
    if ($alive.Count -eq 0) { break }
    Start-Sleep -Milliseconds 100
}
$alive = @($targets | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
if ($alive.Count -gt 0) {
    Warn "有进程未退出，强制结束…"
    foreach ($procId in $alive) {
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    }
}

Say "已停止。"
