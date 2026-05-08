param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram"
)

Set-ExecutionPolicy -Scope Process Bypass -Force
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Set-Location $ProjectRoot
$PyExe = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $PyExe)) { $PyExe = 'python' }
$env:PYTHONPATH = $ProjectRoot

Write-Host "=== RUN DEPENDENCY-AWARE VERIFY ===" -ForegroundColor Cyan
& $PyExe ".\jarvis_stage3_artifacts\temp\dependency_bundle_probe_depplan_20260423_223408_3bdd4117_verify.py"
if ($LASTEXITCODE -ne 0) { throw "dependency-aware verify failed" }

Write-Host "DEPENDENCY-AWARE PATCH SMOKE OK" -ForegroundColor Green
