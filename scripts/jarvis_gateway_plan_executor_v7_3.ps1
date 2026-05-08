param(
    [string]$ProjectRoot,
    [int]$Limit = 8
)

$PyExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PyExe)) { $PyExe = "python" }

$Tool = Join-Path $ProjectRoot "tools\jarvis_gateway_plan_executor_v7_3.py"
$OutDir = Join-Path $ProjectRoot "jarvis_stage3_artifacts\gateway_plan_executor_v7_3"

Write-Host "=== V7.3 EXECUTOR ===" -ForegroundColor Cyan

& $PyExe $Tool `
    --project-root $ProjectRoot `
    --out-dir $OutDir `
    --limit $Limit