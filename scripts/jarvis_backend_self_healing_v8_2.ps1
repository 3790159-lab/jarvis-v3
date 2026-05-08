param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    [string]$BaseUrl = "http://127.0.0.1:8015",
    [switch]$AutoRestart
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectRoot

$PyExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PyExe)) { $PyExe = "python" }

$Tool = Join-Path $ProjectRoot "tools\jarvis_backend_self_healing_v8_2.py"
$OutDir = Join-Path $ProjectRoot "jarvis_stage3_artifacts\backend_self_healing_v8_2"

$Args = @(
    "--project-root", $ProjectRoot,
    "--out-dir", $OutDir,
    "--base-url", $BaseUrl
)

if ($AutoRestart) {
    $Args += "--auto-restart"
}

Write-Host "=== JARVIS BACKEND SELF-HEALING V8.2 ===" -ForegroundColor Cyan
& $PyExe $Tool @Args

exit $LASTEXITCODE