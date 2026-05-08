param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    [int]$MaxIterations = 4,
    [switch]$StrictFailure
)

Set-ExecutionPolicy -Scope Process Bypass -Force
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Set-Location $ProjectRoot

$PyExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $PyExe)) {
    $PyExe = "python"
}

$env:PYTHONPATH = $ProjectRoot

$Code = @"
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(r"$ProjectRoot")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.jarvis_unified_night_bridge import run_cli

payload = run_cli(
    project_root=str(PROJECT_ROOT),
    max_iterations=$MaxIterations,
    degrade_on_failure=$([string](! $StrictFailure)).ToLower(),
)
print(json.dumps(payload, ensure_ascii=False, indent=2))
"@

& $PyExe -c $Code
if ($LASTEXITCODE -ne 0) {
    throw "jarvis_unified_night_bridge run failed"
}