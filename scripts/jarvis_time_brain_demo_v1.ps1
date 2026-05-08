param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram"
)

Set-ExecutionPolicy -Scope Process Bypass -Force
Set-Location $ProjectRoot

$PyExe = ".\.venv\Scripts\python.exe"
$env:PYTHONPATH = $ProjectRoot

Write-Host "=== JARVIS TIME BRAIN DEMO V1 ===" -ForegroundColor Cyan

$DemoPy = @"
from app.services.time_brain import add_scheduled_task, get_time_context
from datetime import UTC, datetime, timedelta
import json

due = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()

task = add_scheduled_task(
    task_id="demo_time_brain_due_task",
    title="DEMO: Time Brain due task",
    due_at=due,
    timezone="Europe/Kyiv",
    lane="time_brain",
    priority=10,
    risk="low",
    details="This task proves Jarvis can understand time, detect due tasks, dispatch them into the gateway queue, and execute them safely.",
    gateway_plan=[
        {
            "tool": "md_report",
            "args": {
                "path": "jarvis_stage3_artifacts/time_brain/demo_time_task_report.md",
                "title": "Time Brain Demo Task",
                "body": "Jarvis executed a task based on scheduled time."
            }
        }
    ],
)

print(json.dumps(get_time_context(), ensure_ascii=False, indent=2))
print(json.dumps(task, ensure_ascii=False, indent=2))
"@

$RunPath = "jarvis_stage3_artifacts\time_brain\demo_schedule.py"
[System.IO.File]::WriteAllText((Join-Path (Resolve-Path ".").Path $RunPath), $DemoPy, [System.Text.UTF8Encoding]::new($false))

& $PyExe $RunPath
if ($LASTEXITCODE -ne 0) {
    throw "Demo schedule failed"
}

& ".\scripts\jarvis_time_brain_tick_v1.ps1" -ProjectRoot $ProjectRoot -Limit 5 -RunGateway