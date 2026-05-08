param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

Set-ExecutionPolicy -Scope Process Bypass -Force
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Set-Location $ProjectRoot

$PyExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $PyExe)) { $PyExe = "python" }

$env:PYTHONPATH = $ProjectRoot

Write-Host "=== PY_COMPILE ===" -ForegroundColor Cyan
& $PyExe -m py_compile ".\app\services\jarvis_quality_hardening.py"
if ($LASTEXITCODE -ne 0) { throw "py_compile failed for jarvis_quality_hardening.py" }

& $PyExe -m py_compile ".\jarvis_stage3_artifacts\temp\jarvis_quality_hardening_verify.py"
if ($LASTEXITCODE -ne 0) { throw "py_compile failed for verify" }

Write-Host ""
Write-Host "=== VERIFY QUALITY HARDENING ===" -ForegroundColor Cyan
& $PyExe ".\jarvis_stage3_artifacts\temp\jarvis_quality_hardening_verify.py"
if ($LASTEXITCODE -ne 0) { throw "quality hardening verify failed" }

Write-Host ""
Write-Host "=== BACKEND API HEALTH ===" -ForegroundColor Cyan
Invoke-RestMethod -Method GET -Uri "$BaseUrl/health" -TimeoutSec 20 | ConvertTo-Json -Depth 10

Write-Host ""
Write-Host "=== UNIFIED NIGHT API HEALTH ===" -ForegroundColor Cyan
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/unified-night/health" -TimeoutSec 20 | ConvertTo-Json -Depth 10

Write-Host ""
Write-Host "QUALITY HARDENING VERIFIED" -ForegroundColor Green
Write-Host "Artifacts:" -ForegroundColor Yellow
Write-Host (Join-Path $ProjectRoot "jarvis_stage3_artifacts\quality_hardening") -ForegroundColor Yellow