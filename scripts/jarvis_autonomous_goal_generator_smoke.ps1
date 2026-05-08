Set-ExecutionPolicy -Scope Process Bypass -Force
$ErrorActionPreference = "Stop"

$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram"
Set-Location $ProjectRoot

$PyExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PyExe)) { $PyExe = "python" }

$env:PYTHONPATH = $ProjectRoot
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

& $PyExe -m py_compile ".\app\services\jarvis_autonomous_goal_generator.py"
if ($LASTEXITCODE -ne 0) { throw "goal generator compile failed" }

$TempPy = Join-Path $ProjectRoot "jarvis_stage3_artifacts\temp\goal_generator_smoke.py"
New-Item -ItemType Directory -Path (Split-Path $TempPy -Parent) -Force | Out-Null

$Py = @"
from pathlib import Path
import sys, json
PROJECT_ROOT = Path(r'''$ProjectRoot''')
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.jarvis_autonomous_goal_generator import JarvisAutonomousGoalGenerator

gen = JarvisAutonomousGoalGenerator(PROJECT_ROOT)
goals = gen.generate_goals(count=5, reason="smoke")
status = gen.status()
next_task = gen.next_goal_task("fallback task")

print(json.dumps({
    "generated": [g.__dict__ for g in goals],
    "status": status,
    "next_task": next_task
}, ensure_ascii=False, indent=2, default=str))

assert len(goals) >= 3
assert "Autonomous strategic goal" in next_task
assert status["queue_size"] >= 0
"@

[System.IO.File]::WriteAllText($TempPy, $Py, [System.Text.UTF8Encoding]::new($false))
& $PyExe $TempPy
if ($LASTEXITCODE -ne 0) { throw "goal generator smoke failed" }

Write-Host "AUTONOMOUS GOAL GENERATOR SMOKE OK" -ForegroundColor Green