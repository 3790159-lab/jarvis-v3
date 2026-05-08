param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    [switch]$IncludeHistory
)

Set-ExecutionPolicy -Scope Process Bypass -Force
$ErrorActionPreference = "Stop"
Set-Location $ProjectRoot

$PyExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PyExe)) { $PyExe = "python" }

$LogDir = Join-Path $ProjectRoot "jarvis_stage3_artifacts\task_completion_watcher_logs"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$OutLog = Join-Path $LogDir "watcher_v2_out_$Stamp.log"
$ErrLog = Join-Path $LogDir "watcher_v2_err_$Stamp.log"

Write-Host "=== STOP OLD WATCHERS ===" -ForegroundColor Cyan
Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -like "*jarvis_task_completion_watcher.py*" } |
    ForEach-Object {
        Write-Host "Stopping PID $($_.ProcessId)"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }

$Args = @("-u", ".\scripts\jarvis_task_completion_watcher.py")
if ($IncludeHistory) { $Args += "--include-history" }

Write-Host ""
Write-Host "=== START TASK COMPLETION WATCHER V2 ===" -ForegroundColor Cyan

$Proc = Start-Process `
    -FilePath $PyExe `
    -ArgumentList $Args `
    -WorkingDirectory $ProjectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog `
    -PassThru

Write-Host "Watcher v2 started."
Write-Host "PID: $($Proc.Id)"
Write-Host "OutLog: $OutLog"
Write-Host "ErrLog: $ErrLog"
Write-Host "History mode: $IncludeHistory"