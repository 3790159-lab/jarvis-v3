Set-ExecutionPolicy -Scope Process Bypass -Force
$ErrorActionPreference = "Continue"

Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -like "*jarvis_task_completion_watcher.py*" } |
    ForEach-Object {
        Write-Host "Stopping watcher PID $($_.ProcessId)"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }