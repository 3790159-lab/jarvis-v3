param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    [switch]$Restore
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectRoot

$PyExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PyExe)) { $PyExe = "python" }

$Tool = Join-Path $ProjectRoot "tools\jarvis_queue_guard_v7_7.py"
$OutDir = Join-Path $ProjectRoot "jarvis_stage3_artifacts\queue_guard_v7_7"

Write-Host "=== JARVIS QUEUE GUARD V7.7 ===" -ForegroundColor Cyan

$Args = @(
  "--project-root", $ProjectRoot,
  "--out-dir", $OutDir
)

if ($Restore) {
  $Args += "--restore"
}

& $PyExe $Tool @Args

exit $LASTEXITCODE