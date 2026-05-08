param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    [switch]$RunApi
)

Set-ExecutionPolicy -Scope Process Bypass -Force
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Set-Location $ProjectRoot

$PyExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $PyExe)) { $PyExe = "python" }

$env:PYTHONPATH = $ProjectRoot
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

Write-Host "=== PY_COMPILE ===" -ForegroundColor Cyan
& $PyExe -m py_compile ".\app\services\jarvis_brain_executor.py"
if ($LASTEXITCODE -ne 0) { throw "brain executor compile failed" }

& $PyExe -m py_compile ".\app\routers\jarvis_brain_executor_router.py"
if ($LASTEXITCODE -ne 0) { throw "brain executor router compile failed" }

Write-Host ""
Write-Host "=== LOCAL BRAIN EXECUTOR RUN ===" -ForegroundColor Cyan

$TempDir = Join-Path $ProjectRoot "jarvis_stage3_artifacts\temp"
if (-not (Test-Path $TempDir)) { New-Item -ItemType Directory -Path $TempDir -Force | Out-Null }

$TaskPath = Join-Path $TempDir "brain_executor_task.txt"
$TempPy = Join-Path $TempDir "jarvis_brain_executor_smoke.py"

[System.IO.File]::WriteAllText(
    $TaskPath,
    "Create dynamic n8n pipeline: webhook, validate data, external API, logical decision and final report",
    [System.Text.UTF8Encoding]::new($false)
)

$PyContent = @"
from pathlib import Path
import sys
import json

PROJECT_ROOT = Path(r'''$ProjectRoot''')
TASK_PATH = Path(r'''$TaskPath''')

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.jarvis_brain_executor import JarvisBrainExecutor

task = TASK_PATH.read_text(encoding="utf-8")

executor = JarvisBrainExecutor(PROJECT_ROOT)
result = executor.execute(task)

print(result.human_summary)
print(json.dumps({
    "status": result.status,
    "execution_id": result.execution_id,
    "primary_result": result.primary_result,
    "next_actions": result.next_actions,
}, ensure_ascii=False, indent=2, default=str))

if result.status not in {"completed", "completed_with_warnings"}:
    raise SystemExit(2)
"@

[System.IO.File]::WriteAllText($TempPy, $PyContent, [System.Text.UTF8Encoding]::new($false))

& $PyExe $TempPy
if ($LASTEXITCODE -ne 0) { throw "local brain executor smoke failed" }

if ($RunApi) {
    Write-Host ""
    Write-Host "=== API HEALTH ===" -ForegroundColor Cyan
    Invoke-RestMethod -Method GET -Uri "http://127.0.0.1:8015/api/brain-executor/health" -TimeoutSec 30 | ConvertTo-Json -Depth 20

    Write-Host ""
    Write-Host "=== API RUN UTF8 BODY ===" -ForegroundColor Cyan

    $Payload = @{
        task = "Create dynamic n8n pipeline: webhook, validate data, external API, logical decision and final report"
        dry_run = $false
    }

    $Json = $Payload | ConvertTo-Json -Depth 10
    $Bytes = [System.Text.Encoding]::UTF8.GetBytes($Json)

    Invoke-RestMethod `
        -Method POST `
        -Uri "http://127.0.0.1:8015/api/brain-executor/run" `
        -ContentType "application/json; charset=utf-8" `
        -Body $Bytes `
        -TimeoutSec 180 | ConvertTo-Json -Depth 30
}

Write-Host ""
Write-Host "JARVIS BRAIN EXECUTOR SMOKE OK" -ForegroundColor Green