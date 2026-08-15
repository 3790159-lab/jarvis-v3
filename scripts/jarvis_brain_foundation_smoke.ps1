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

Write-Host "=== PY_COMPILE ===" -ForegroundColor Cyan
& $PyExe -m py_compile ".\app\services\jarvis_brain_foundation.py"
if ($LASTEXITCODE -ne 0) { throw "brain service compile failed" }

& $PyExe -m py_compile ".\app\routers\jarvis_brain_router.py"
if ($LASTEXITCODE -ne 0) { throw "brain router compile failed" }

Write-Host ""
Write-Host "=== LOCAL BRAIN COMPILE ===" -ForegroundColor Cyan

$TempDir = Join-Path $ProjectRoot "jarvis_stage3_artifacts\temp"
if (-not (Test-Path $TempDir)) { New-Item -ItemType Directory -Path $TempDir -Force | Out-Null }
$TempPy = Join-Path $TempDir "jarvis_brain_smoke.py"

$PyContent = @"
from pathlib import Path
import sys, json
PROJECT_ROOT = Path(r'''$ProjectRoot''')
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.jarvis_brain_foundation import JarvisBrainFoundation

brain = JarvisBrainFoundation(PROJECT_ROOT)
compiled = brain.compile_task(
    "Создай динамический n8n pipeline: webhook, проверка данных, внешний API, логическое решение, Telegram отчёт и сохранение результата"
)
print(brain.summary(compiled))
print(json.dumps(compiled.__dict__, ensure_ascii=False, indent=2, default=str))

assert compiled.intent == "automation_pipeline"
assert "n8n_super_agent" in compiled.tools
assert compiled.selected_provider in {"openai", "anthropic", "ollama", "rule_based", "gemini"}
"@

[System.IO.File]::WriteAllText($TempPy, $PyContent, [System.Text.UTF8Encoding]::new($false))
& $PyExe $TempPy
if ($LASTEXITCODE -ne 0) { throw "local brain compile smoke failed" }

if ($RunApi) {
    Write-Host ""
    Write-Host "=== API HEALTH ===" -ForegroundColor Cyan
    Invoke-RestMethod -Method GET -Uri "http://127.0.0.1:8015/api/brain/health" -TimeoutSec 30 | ConvertTo-Json -Depth 20

    Write-Host ""
    Write-Host "=== API COMPILE ===" -ForegroundColor Cyan
    $Body = @{
        task = "Создай умный pipeline для n8n, Telegram и внешнего API, с проверкой результата и rollback планом"
    } | ConvertTo-Json -Depth 10

    Invoke-RestMethod `
        -Method POST `
        -Uri "http://127.0.0.1:8015/api/brain/compile" `
        -ContentType "application/json" `
        -Body $Body `
        -TimeoutSec 60 | ConvertTo-Json -Depth 30
}

Write-Host ""
Write-Host "JARVIS BRAIN FOUNDATION SMOKE OK" -ForegroundColor Green