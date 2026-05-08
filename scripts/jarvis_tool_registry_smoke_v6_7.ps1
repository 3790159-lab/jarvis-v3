param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram"
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectRoot

$PyExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PyExe)) { $PyExe = "python" }

$Tool = Join-Path $ProjectRoot "tools\jarvis_tool_registry_v6_7.py"
$OutDir = Join-Path $ProjectRoot "jarvis_stage3_artifacts\tool_registry_v6_7"

Write-Host "=== JARVIS TOOL REGISTRY V6.7 SMOKE ===" -ForegroundColor Cyan

& $PyExe $Tool `
    --project-root $ProjectRoot `
    --out-dir $OutDir `
    --smoke

$Exit = $LASTEXITCODE

Write-Host ""
Write-Host "Latest smoke report:" -ForegroundColor Green
Write-Host (Join-Path $OutDir "latest_tool_registry_smoke.md")

exit $Exit