param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectRoot

$PyExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PyExe)) {
    $PyExe = "python"
}

$ToolPath = Join-Path $ProjectRoot "tools\jarvis_real_action_evidence_v6_3.py"
$OutDir = Join-Path $ProjectRoot "jarvis_stage3_artifacts\real_action_evidence"

if (-not (Test-Path $ToolPath)) {
    throw "Missing evidence tool: $ToolPath"
}

Write-Host "=== JARVIS REAL ACTION EVIDENCE V6.3 ===" -ForegroundColor Cyan
Write-Host "ProjectRoot: $ProjectRoot"
Write-Host "BaseUrl:     $BaseUrl"
Write-Host ""

& $PyExe $ToolPath `
    --project-root $ProjectRoot `
    --base-url $BaseUrl `
    --out-dir $OutDir `
    --timeout 5

$Exit = $LASTEXITCODE

Write-Host ""
Write-Host "Latest JSON:" -ForegroundColor Yellow
Write-Host (Join-Path $OutDir "latest_real_action_evidence_report.json")

Write-Host ""
Write-Host "Latest MD:" -ForegroundColor Yellow
Write-Host (Join-Path $OutDir "latest_real_action_evidence_summary.md")

if ($Exit -eq 0) {
    Write-Host ""
    Write-Host "REAL ACTION EVIDENCE PASSED." -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "REAL ACTION EVIDENCE NEEDS REVIEW. ExitCode=$Exit" -ForegroundColor Yellow
}

exit $Exit