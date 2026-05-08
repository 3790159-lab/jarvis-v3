param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectRoot

$PyExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PyExe)) { $PyExe = "python" }

$EvidenceScript = Join-Path $ProjectRoot "scripts\jarvis_real_action_evidence_v6_3.ps1"
$BrainTool = Join-Path $ProjectRoot "tools\jarvis_strategic_brain_v6_4.py"
$OutDir = Join-Path $ProjectRoot "jarvis_stage3_artifacts\strategic_brain_v6_4"
$StateDir = Join-Path $ProjectRoot "state\jarvis_brain"

Write-Host "=== JARVIS V6.4 STRATEGIC BRAIN CYCLE ===" -ForegroundColor Cyan

if (Test-Path $EvidenceScript) {
    Write-Host ""
    Write-Host "Step 1/2: Refresh real action evidence..." -ForegroundColor Yellow
    & $EvidenceScript -ProjectRoot $ProjectRoot -BaseUrl $BaseUrl
    $EvidenceExit = $LASTEXITCODE

    if ($EvidenceExit -ne 0) {
        Write-Host "Evidence returned non-zero. Brain will still plan safe review tasks." -ForegroundColor Yellow
    }
} else {
    Write-Host "Evidence script missing. Brain will plan evidence bootstrap task." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Step 2/2: Run strategic brain planner..." -ForegroundColor Yellow

& $PyExe $BrainTool `
    --project-root $ProjectRoot `
    --out-dir $OutDir `
    --state-dir $StateDir

$Exit = $LASTEXITCODE

Write-Host ""
Write-Host "Latest brain report:" -ForegroundColor Green
Write-Host (Join-Path $OutDir "latest_strategic_brain_report.md")

Write-Host ""
Write-Host "Queue file:" -ForegroundColor Green
Write-Host (Join-Path $StateDir "action_queue_v6_4.json")

exit $Exit