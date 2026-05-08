param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    [int]$Limit = 3
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectRoot

$PyExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PyExe)) { $PyExe = "python" }

$Executor = Join-Path $ProjectRoot "tools\jarvis_queue_executor_v6_5.py"
$StateDir = Join-Path $ProjectRoot "state\jarvis_brain"
$OutDir = Join-Path $ProjectRoot "jarvis_stage3_artifacts\queue_executor_v6_5"

Write-Host "=== JARVIS QUEUE EXECUTOR V6.5 ===" -ForegroundColor Cyan

& $PyExe $Executor `
    --project-root $ProjectRoot `
    --state-dir $StateDir `
    --out-dir $OutDir `
    --limit $Limit

$Exit = $LASTEXITCODE

Write-Host ""
Write-Host "Latest executor report:" -ForegroundColor Green
Write-Host (Join-Path $OutDir "latest_queue_executor_report.md")

Write-Host ""
Write-Host "Updated queue:" -ForegroundColor Green
Write-Host (Join-Path $StateDir "action_queue_v6_4.json")

exit $Exit