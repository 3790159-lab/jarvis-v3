param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram"
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectRoot

$PyExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PyExe)) { $PyExe = "python" }

$Tool = Join-Path $ProjectRoot "tools\jarvis_n8n_pipeline_creator_v7_5.py"
$OutDir = Join-Path $ProjectRoot "jarvis_stage3_artifacts\n8n_pipeline_creator_v7_5"

Write-Host "=== JARVIS N8N PIPELINE CREATOR V7.5 ===" -ForegroundColor Cyan

& $PyExe $Tool `
  --project-root $ProjectRoot `
  --out-dir $OutDir

exit $LASTEXITCODE