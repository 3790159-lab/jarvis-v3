param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    [int]$MaxNew = 8
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectRoot

$PyExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PyExe)) { $PyExe = "python" }

$Tool = Join-Path $ProjectRoot "tools\jarvis_auto_task_generator_v7_2.py"
$OutDir = Join-Path $ProjectRoot "jarvis_stage3_artifacts\auto_task_generator_v7_2"

Write-Host "=== JARVIS AUTO TASK GENERATOR V7.2 ===" -ForegroundColor Cyan

& $PyExe $Tool `
  --project-root $ProjectRoot `
  --out-dir $OutDir `
  --max-new $MaxNew

exit $LASTEXITCODE