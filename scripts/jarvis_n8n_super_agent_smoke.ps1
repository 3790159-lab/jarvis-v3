param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    [string]$BaseUrl = "http://127.0.0.1:8015",
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
& $PyExe -m py_compile ".\app\services\jarvis_n8n_super_agent.py"
if ($LASTEXITCODE -ne 0) { throw "n8n super agent service compile failed" }

& $PyExe -m py_compile ".\app\routers\jarvis_n8n_super_agent_router.py"
if ($LASTEXITCODE -ne 0) { throw "n8n super agent router compile failed" }

Write-Host ""
Write-Host "=== LOCAL SUPER AGENT RUN ===" -ForegroundColor Cyan

$TempDir = Join-Path $ProjectRoot "jarvis_stage3_artifacts\temp"
if (-not (Test-Path $TempDir)) { New-Item -ItemType Directory -Path $TempDir -Force | Out-Null }
$TempPy = Join-Path $TempDir "n8n_super_agent_smoke.py"

$PyContent = @"
from pathlib import Path
import sys
import json

PROJECT_ROOT = Path(r'''$ProjectRoot''')
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.jarvis_n8n_super_agent import JarvisN8nSuperAgent

agent = JarvisN8nSuperAgent(PROJECT_ROOT)
result = agent.run(
    user_task="Create a night report logger workflow and test it",
    workflow_kind="night_report_logger",
    activate=True,
    test_webhook=True,
)
print(result.summary)
print("")
print(json.dumps(result.__dict__, ensure_ascii=False, indent=2, default=str))

if result.status not in {"tested", "active"}:
    raise SystemExit(2)
"@

[System.IO.File]::WriteAllText($TempPy, $PyContent, [System.Text.UTF8Encoding]::new($false))
& $PyExe $TempPy
if ($LASTEXITCODE -ne 0) { throw "local n8n super agent run failed" }

if ($RunApi) {
    Write-Host ""
    Write-Host "=== API HEALTH ===" -ForegroundColor Cyan
    Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/n8n-super-agent/health" -TimeoutSec 30 | ConvertTo-Json -Depth 20

    Write-Host ""
    Write-Host "=== API RUN ===" -ForegroundColor Cyan
    $Body = @{
        task = "Create Telegram operator alert workflow and test it"
        workflow_kind = "telegram_operator_alert"
        activate = $true
        test_webhook = $true
    } | ConvertTo-Json -Depth 10

    Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/n8n-super-agent/run" -ContentType "application/json" -Body $Body -TimeoutSec 120 | ConvertTo-Json -Depth 30
}

Write-Host ""
Write-Host "N8N SUPER AGENT SMOKE OK" -ForegroundColor Green
Write-Host "Artifacts:" -ForegroundColor Yellow
Write-Host (Join-Path $ProjectRoot "jarvis_stage3_artifacts\n8n_super_agent") -ForegroundColor Yellow