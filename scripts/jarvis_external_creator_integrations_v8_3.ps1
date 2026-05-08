param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    [switch]$AddQueue
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectRoot

$PyExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PyExe)) { $PyExe = "python" }

$Tool = Join-Path $ProjectRoot "tools\jarvis_external_creator_integrations_v8_3.py"
$OutDir = Join-Path $ProjectRoot "jarvis_stage3_artifacts\external_creator_integrations_v8_3"

$Args = @(
  "--project-root", $ProjectRoot,
  "--out-dir", $OutDir
)

if ($AddQueue) {
  $Args += "--add-queue"
}

Write-Host "=== JARVIS EXTERNAL CREATOR INTEGRATIONS V8.3 ===" -ForegroundColor Cyan
& $PyExe $Tool @Args

exit $LASTEXITCODE