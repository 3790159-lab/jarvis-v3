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

Write-Host "=== CHECK .env N8N CONFIG ===" -ForegroundColor Cyan
Select-String -Path ".\.env" -Pattern "N8N_BASE_URL|N8N_API_KEY|JARVIS_N8N_BASE_URL" -ErrorAction SilentlyContinue | ForEach-Object {
    if ($_.Line -match "N8N_API_KEY=") {
        Write-Host "N8N_API_KEY=***present***" -ForegroundColor Yellow
    } else {
        Write-Host $_.Line -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "=== PY_COMPILE ===" -ForegroundColor Cyan
& $PyExe -m py_compile ".\app\services\jarvis_n8n_specialist.py"
if ($LASTEXITCODE -ne 0) { throw "n8n specialist service compile failed" }

& $PyExe -m py_compile ".\app\routers\jarvis_n8n_specialist_router.py"
if ($LASTEXITCODE -ne 0) { throw "n8n specialist router compile failed" }

Write-Host ""
Write-Host "=== LOCAL SPECIALIST TEST ===" -ForegroundColor Cyan

$TempDir = Join-Path $ProjectRoot "jarvis_stage3_artifacts\temp"
if (-not (Test-Path $TempDir)) { New-Item -ItemType Directory -Path $TempDir -Force | Out-Null }

$TempPy = Join-Path $TempDir "n8n_specialist_local_smoke.py"

$PyContent = @"
from pathlib import Path
import sys

PROJECT_ROOT = Path(r'''$ProjectRoot''')
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.jarvis_n8n_specialist import JarvisN8nSpecialist

agent = JarvisN8nSpecialist(PROJECT_ROOT)
result = agent.handle_task(
    task="Prepare Jarvis n8n workflow blueprint",
    deploy=False,
    test_webhook=False,
)
print(agent.format_human_summary(result))
"@

[System.IO.File]::WriteAllText($TempPy, $PyContent, [System.Text.UTF8Encoding]::new($false))

& $PyExe $TempPy
if ($LASTEXITCODE -ne 0) { throw "local n8n specialist test failed" }

if ($RunApi) {
    Write-Host ""
    Write-Host "=== API HEALTH ===" -ForegroundColor Cyan
    Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/n8n-specialist/health" -TimeoutSec 30 | ConvertTo-Json -Depth 20

    Write-Host ""
    Write-Host "=== API RUN ===" -ForegroundColor Cyan
    $Body = @{
        task = "Prepare Jarvis n8n workflow blueprint"
        deploy = $false
        test_webhook = $false
    } | ConvertTo-Json -Depth 10

    Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/n8n-specialist/run" -ContentType "application/json" -Body $Body -TimeoutSec 60 | ConvertTo-Json -Depth 30
}

Write-Host ""
Write-Host "N8N SPECIALIST SMOKE OK" -ForegroundColor Green
Write-Host "Artifacts:" -ForegroundColor Yellow
Write-Host (Join-Path $ProjectRoot "jarvis_stage3_artifacts\n8n_specialist") -ForegroundColor Yellow