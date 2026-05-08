param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    [switch]$RunApi,
    [switch]$TestTelegramSend
)

Set-ExecutionPolicy -Scope Process Bypass -Force
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Set-Location $ProjectRoot

$PyExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $PyExe)) { $PyExe = "python" }

$env:PYTHONPATH = $ProjectRoot

Write-Host "=== COMPILE ===" -ForegroundColor Cyan
& $PyExe -m py_compile ".\app\services\jarvis_n8n_super_agent.py"
if ($LASTEXITCODE -ne 0) { throw "compile failed" }

Write-Host ""
Write-Host "=== EXTERNAL HTTP REQUEST WORKFLOW ===" -ForegroundColor Cyan

$TempDir = Join-Path $ProjectRoot "jarvis_stage3_artifacts\temp"
if (-not (Test-Path $TempDir)) { New-Item -ItemType Directory -Path $TempDir -Force | Out-Null }

$TempPy = Join-Path $TempDir "n8n_external_nodes_smoke.py"

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
    user_task="Create external API request workflow and test it",
    workflow_kind="http_request_probe",
    activate=True,
    test_webhook=True,
)

print(result.summary)
print("")
print(json.dumps({
    "status": result.status,
    "workflow_kind": result.workflow_kind,
    "workflow_id": result.workflow_id,
    "webhook_test_ok": bool(result.webhook_test_result and result.webhook_test_result.get("ok")),
    "webhook_test_result": result.webhook_test_result,
}, ensure_ascii=False, indent=2, default=str))

if result.status not in {"tested", "active"}:
    raise SystemExit(2)
"@

[System.IO.File]::WriteAllText($TempPy, $PyContent, [System.Text.UTF8Encoding]::new($false))
& $PyExe $TempPy
if ($LASTEXITCODE -ne 0) { throw "external HTTP node workflow failed" }

if ($RunApi) {
    Write-Host ""
    Write-Host "=== BACKEND API HEALTH ===" -ForegroundColor Cyan
    Invoke-RestMethod -Method GET -Uri "http://127.0.0.1:8015/api/n8n-super-agent/health" -TimeoutSec 30 | ConvertTo-Json -Depth 20

    Write-Host ""
    Write-Host "=== BACKEND API RUN ===" -ForegroundColor Cyan
    $Body = @{
        task = "Create external HTTP request workflow and test it"
        workflow_kind = "http_request_probe"
        activate = $true
        test_webhook = $true
    } | ConvertTo-Json -Depth 10

    Invoke-RestMethod `
        -Method POST `
        -Uri "http://127.0.0.1:8015/api/n8n-super-agent/run" `
        -ContentType "application/json" `
        -Body $Body `
        -TimeoutSec 160 | ConvertTo-Json -Depth 30
}

if ($TestTelegramSend) {
    Write-Host ""
    Write-Host "=== TELEGRAM SEND WORKFLOW DEPLOY ONLY ===" -ForegroundColor Cyan

    $TempPy2 = Join-Path $TempDir "n8n_telegram_send_workflow_smoke.py"

    $PyContent2 = @"
from pathlib import Path
import sys
import json

PROJECT_ROOT = Path(r'''$ProjectRoot''')
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.jarvis_n8n_super_agent import JarvisN8nSuperAgent

agent = JarvisN8nSuperAgent(PROJECT_ROOT)

result = agent.run(
    user_task="Create Telegram send message workflow",
    workflow_kind="telegram_send_message",
    activate=True,
    test_webhook=False,
)

print(result.summary)
print(json.dumps({
    "status": result.status,
    "workflow_kind": result.workflow_kind,
    "workflow_id": result.workflow_id,
    "production_webhook": result.plan.get("production_webhook_url"),
}, ensure_ascii=False, indent=2, default=str))

if result.status not in {"active", "tested", "deployed"}:
    raise SystemExit(2)
"@

    [System.IO.File]::WriteAllText($TempPy2, $PyContent2, [System.Text.UTF8Encoding]::new($false))
    & $PyExe $TempPy2
    if ($LASTEXITCODE -ne 0) { throw "telegram send workflow deploy failed" }
}

Write-Host ""
Write-Host "N8N EXTERNAL NODES SMOKE OK" -ForegroundColor Green