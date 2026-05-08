param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    [string]$Idea = "",
    [switch]$AddQueue
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectRoot

$PyExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PyExe)) { $PyExe = "python" }

$Tool = Join-Path $ProjectRoot "tools\jarvis_full_creator_v8_0.py"
$OutDir = Join-Path $ProjectRoot "jarvis_stage3_artifacts\full_creator_v8_0"

Write-Host "=== JARVIS FULL CREATOR V8.0 ===" -ForegroundColor Cyan

$Args = @(
    "--project-root", $ProjectRoot,
    "--out-dir", $OutDir,
    "--idea", $Idea
)

if ($AddQueue) {
    $Args += "--add-queue"
}

& $PyExe $Tool @Args

exit $LASTEXITCODE