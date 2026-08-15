param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram"
)

Set-ExecutionPolicy -Scope Process Bypass -Force
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Set-Location $ProjectRoot

$LogDir = Join-Path $ProjectRoot "jarvis_stage3_artifacts\telegram_bridge_logs"
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$OutLog = Join-Path $LogDir "telegram_bridge_out_$Stamp.log"
$ErrLog = Join-Path $LogDir "telegram_bridge_err_$Stamp.log"

Write-Host "=== STOP OLD TELEGRAM BRIDGE PROCESSES ===" -ForegroundColor Cyan

$Old = Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -match "python" -and
        $_.CommandLine -match "jarvis_operator_telegram_bridge.py"
    }

foreach ($p in $Old) {
    Write-Host ("Stopping PID " + $p.ProcessId) -ForegroundColor Yellow
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}

Start-Sleep -Seconds 2

Write-Host ""
Write-Host "=== START TELEGRAM BRIDGE DETACHED ===" -ForegroundColor Cyan

$PyExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $PyExe)) {
    $PyExe = "python"
}

$Proc = Start-Process `
    -FilePath $PyExe `
    -ArgumentList @(".\scripts\jarvis_operator_telegram_bridge.py") `
    -WorkingDirectory $ProjectRoot `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog `
    -PassThru

Start-Sleep -Seconds 3

if ($Proc.HasExited) {
    Write-Host "Telegram bridge exited early." -ForegroundColor Red
    Write-Host "=== STDERR ===" -ForegroundColor Red
    if (Test-Path $ErrLog) { Get-Content $ErrLog -Tail 80 }
    throw "Telegram bridge failed to start"
}

Write-Host "Telegram bridge started detached." -ForegroundColor Green
Write-Host "PID: $($Proc.Id)" -ForegroundColor Green
Write-Host "OutLog: $OutLog" -ForegroundColor Cyan
Write-Host "ErrLog: $ErrLog" -ForegroundColor Cyan

Write-Host ""
Write-Host "Test in Telegram:" -ForegroundColor Yellow
Write-Host "/help" -ForegroundColor Yellow
Write-Host "Что ты умеешь?" -ForegroundColor Yellow