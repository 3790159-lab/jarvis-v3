param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram"
)
$ErrorActionPreference = "Stop"
Set-Location $ProjectRoot
$PyExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PyExe)) { $PyExe = "python" }
$Tool = Join-Path $ProjectRoot "tools\jarvis_tool_registry_v6_9.py"
$OutDir = Join-Path $ProjectRoot "jarvis_stage3_artifacts\tool_registry_v6_9"

Write-Host "=== JARVIS TOOL REGISTRY V6.9 SMOKE ===" -ForegroundColor Cyan
& $PyExe $Tool --project-root $ProjectRoot --out-dir $OutDir --smoke
exit $LASTEXITCODE