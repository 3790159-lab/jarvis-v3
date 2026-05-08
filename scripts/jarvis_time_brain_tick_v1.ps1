param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    [int]$Limit = 20,
    [switch]$RunGateway
)

Set-ExecutionPolicy -Scope Process Bypass -Force
Set-Location $ProjectRoot

$PyExe = ".\.venv\Scripts\python.exe"
$env:PYTHONPATH = $ProjectRoot

Write-Host "=== JARVIS TIME BRAIN TICK V1 ===" -ForegroundColor Cyan

$TickPy = @"
from app.services.time_brain import get_time_context, dispatch_due_tasks
import json

print(json.dumps(get_time_context(), ensure_ascii=False, indent=2))
print(json.dumps(dispatch_due_tasks(limit=$Limit), ensure_ascii=False, indent=2))
"@

$RunPath = "jarvis_stage3_artifacts\time_brain\tick_run.py"
[System.IO.File]::WriteAllText((Join-Path (Resolve-Path ".").Path $RunPath), $TickPy, [System.Text.UTF8Encoding]::new($false))

& $PyExe $RunPath
if ($LASTEXITCODE -ne 0) {
    throw "Time brain tick failed"
}

if ($RunGateway) {
    Write-Host "=== RUN SAFE GATEWAY AFTER TIME DISPATCH ===" -ForegroundColor Cyan
    & ".\scripts\jarvis_safe_gateway_plan_executor_v1_3.ps1" -Limit $Limit
    if ($LASTEXITCODE -ne 0) {
        throw "Safe gateway failed after time dispatch"
    }
}