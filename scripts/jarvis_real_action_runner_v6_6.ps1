param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    [int]$Limit = 5
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectRoot

$PyExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PyExe)) { $PyExe = "python" }

$Tool = Join-Path $ProjectRoot "tools\jarvis_real_action_runner_v6_6.py"
$StateDir = Join-Path $ProjectRoot "state\jarvis_brain"
$OutDir = Join-Path $ProjectRoot "jarvis_stage3_artifacts\real_action_runner_v6_6"

Write-Host "=== JARVIS REAL ACTION RUNNER V6.6 ===" -ForegroundColor Cyan

& $PyExe $Tool `
    --project-root $ProjectRoot `
    --state-dir $StateDir `
    --out-dir $OutDir `
    --python $PyExe `
    --limit $Limit

$Exit = $LASTEXITCODE

Write-Host ""
Write-Host "Latest real action report:" -ForegroundColor Green
Write-Host (Join-Path $OutDir "latest_real_action_runner_report.md")

exit $Exit