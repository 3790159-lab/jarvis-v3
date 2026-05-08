param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    [string]$BaseUrl = "http://127.0.0.1:8015"
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

Write-Host "=== PY_COMPILE ===" -ForegroundColor Cyan
& $PyExe -m py_compile ".\app\services\jarvis_external_systems_readiness.py"
if ($LASTEXITCODE -ne 0) { throw "py_compile failed for jarvis_external_systems_readiness.py" }

& $PyExe -m py_compile ".\jarvis_stage3_artifacts\temp\jarvis_external_systems_readiness_verify.py"
if ($LASTEXITCODE -ne 0) { throw "py_compile failed for verify script" }

Write-Host ""
Write-Host "=== VERIFY EXTERNAL SYSTEMS READINESS ===" -ForegroundColor Cyan
& $PyExe ".\jarvis_stage3_artifacts\temp\jarvis_external_systems_readiness_verify.py"
if ($LASTEXITCODE -ne 0) { throw "verify failed" }

Write-Host ""
Write-Host "=== HEALTH CHECKS ===" -ForegroundColor Cyan
$Endpoints = @(
    "$BaseUrl/health",
    "$BaseUrl/api/ai/health",
    "$BaseUrl/api/autonomy/health"
)

foreach ($url in $Endpoints) {
    try {
        $null = Invoke-RestMethod -Method GET -Uri $url -TimeoutSec 15
        Write-Host ("OK: " + $url) -ForegroundColor Green
    }
    catch {
        Write-Host ("WARN: " + $url + " :: " + $_.Exception.Message) -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "BLOCK 5 VERIFIED" -ForegroundColor Green
Write-Host "Artifacts:" -ForegroundColor Yellow
Write-Host (Join-Path $ProjectRoot "jarvis_stage3_artifacts\external_systems_readiness") -ForegroundColor Yellow