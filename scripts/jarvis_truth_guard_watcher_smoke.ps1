Set-ExecutionPolicy -Scope Process Bypass -Force
$ErrorActionPreference = "Stop"

$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram"
Set-Location $ProjectRoot

$PyExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PyExe)) { $PyExe = "python" }

$env:PYTHONPATH = $ProjectRoot
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

Get-ChildItem "$ProjectRoot\app\services" -Recurse -Directory -Filter "__pycache__" |
  Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

& $PyExe -m py_compile ".\app\services\jarvis_truth_guard.py"
if ($LASTEXITCODE -ne 0) { throw "truth guard compile failed" }

$TempPy = Join-Path $ProjectRoot "jarvis_stage3_artifacts\temp\truth_guard_hard_reset_smoke.py"
New-Item -ItemType Directory -Path (Split-Path $TempPy -Parent) -Force | Out-Null

$Py = @"
from pathlib import Path
import sys, json

PROJECT_ROOT = Path(r'''$ProjectRoot''')
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app.services.jarvis_truth_guard as mod
from app.services.jarvis_truth_guard import JarvisTruthGuard

guard = JarvisTruthGuard(PROJECT_ROOT)

fake = guard.guard_summary("Создай гугл таблицу", {"summary": "created"})
real = guard.guard_summary("Создай гугл таблицу", {
    "spreadsheet_url": "https://docs.google.com/spreadsheets/d/abc"
})
n8n = guard.guard_summary("Создай n8n workflow", {
    "workflow_id": "wf123",
    "status": "tested"
})

print("IMPORTED_FROM:", mod.__file__)
print("VERSION:", getattr(mod, "TRUTH_GUARD_VERSION", "missing"))
print(json.dumps({"fake": fake, "real": real, "n8n": n8n}, ensure_ascii=False, indent=2))

assert getattr(mod, "TRUTH_GUARD_VERSION", "") == "hard_reset_google_v3"
assert fake["ok"] is False, fake
assert fake["claim_type"] == "google_sheet_creation", fake
assert fake["evidence"]["google_detected"] is True, fake
assert real["ok"] is True, real
assert n8n["ok"] is True, n8n
"@

[System.IO.File]::WriteAllText($TempPy, $Py, [System.Text.UTF8Encoding]::new($false))
& $PyExe $TempPy
if ($LASTEXITCODE -ne 0) { throw "truth guard hard reset smoke failed" }

Write-Host "TRUTH GUARD HARD RESET SMOKE OK" -ForegroundColor Green
