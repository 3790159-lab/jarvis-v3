Set-ExecutionPolicy -Scope Process Bypass -Force
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram"
Set-Location $ProjectRoot

$PyExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PyExe)) { $PyExe = "python" }

$env:PYTHONPATH = $ProjectRoot
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

Write-Host "=== LOCAL CODE IMPROVEMENT LANE TEST ===" -ForegroundColor Cyan

$TempPy = Join-Path $ProjectRoot "jarvis_stage3_artifacts\temp\code_improvement_lane_smoke.py"

$Py = @"
from pathlib import Path
import sys, json

PROJECT_ROOT = Path(r'''$ProjectRoot''')
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.jarvis_brain_executor import JarvisBrainExecutor

executor = JarvisBrainExecutor(PROJECT_ROOT)
result = executor.execute("Night self-fix: improve UTF-8, fix errors, improve night loop safety and save lessons")

print(result.human_summary)
print(json.dumps({
    "status": result.status,
    "primary_result": result.primary_result,
    "next_actions": result.next_actions,
}, ensure_ascii=False, indent=2, default=str))

if result.status not in {"completed", "completed_with_warnings"}:
    raise SystemExit(2)
"@

[System.IO.File]::WriteAllText($TempPy, $Py, [System.Text.UTF8Encoding]::new($false))
& $PyExe $TempPy
if ($LASTEXITCODE -ne 0) { throw "code improvement lane smoke failed" }

Write-Host ""
Write-Host "CODE IMPROVEMENT LANE SMOKE OK" -ForegroundColor Green