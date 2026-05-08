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

Write-Host "=== COMPILE ===" -ForegroundColor Cyan
& $PyExe -m py_compile ".\app\services\jarvis_n8n_super_agent.py"
if ($LASTEXITCODE -ne 0) { throw "super agent compile failed" }
& $PyExe -m py_compile ".\scripts\jarvis_operator_telegram_bridge.py"
if ($LASTEXITCODE -ne 0) { throw "telegram bridge compile failed" }

Write-Host ""
Write-Host "=== LOCAL DYNAMIC PIPELINE RUN ===" -ForegroundColor Cyan

$TempDir = Join-Path $ProjectRoot "jarvis_stage3_artifacts\temp"
if (-not (Test-Path $TempDir)) { New-Item -ItemType Directory -Path $TempDir -Force | Out-Null }
$TempPy = Join-Path $TempDir "dynamic_pipeline_builder_v4_smoke.py"

$PyContent = @"
from pathlib import Path
import sys, json
PROJECT_ROOT = Path(r'''$ProjectRoot''')
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.jarvis_n8n_super_agent import JarvisN8nSuperAgent

agent = JarvisN8nSuperAgent(PROJECT_ROOT)
result = agent.run(
    user_task="Create dynamic pipeline with webhook, validation, external API, transformation, decision and final operator report",
    workflow_kind="dynamic_pipeline",
    activate=True,
    test_webhook=True,
)
print(result.summary)
print(json.dumps({
    "status": result.status,
    "kind": result.workflow_kind,
    "workflow_id": result.workflow_id,
    "test_ok": bool(result.webhook_test_result and result.webhook_test_result.get("ok")),
}, ensure_ascii=False, indent=2))
if result.status != "tested":
    raise SystemExit(2)
"@

[System.IO.File]::WriteAllText($TempPy, $PyContent, [System.Text.UTF8Encoding]::new($false))
& $PyExe $TempPy
if ($LASTEXITCODE -ne 0) { throw "dynamic pipeline smoke failed" }

if ($RunApi) {
    Write-Host ""
    Write-Host "=== RESTARTED BACKEND API RUN ===" -ForegroundColor Cyan
    $Body = @{
        task = "Create dynamic intelligent multi-service pipeline"
        workflow_kind = "dynamic_pipeline"
        activate = $true
        test_webhook = $true
    } | ConvertTo-Json -Depth 10

    Invoke-RestMethod `
        -Method POST `
        -Uri "http://127.0.0.1:8015/api/n8n-super-agent/run" `
        -ContentType "application/json" `
        -Body $Body `
        -TimeoutSec 180 | ConvertTo-Json -Depth 30
}

Write-Host ""
Write-Host "DYNAMIC PIPELINE BUILDER V4 SMOKE OK" -ForegroundColor Green