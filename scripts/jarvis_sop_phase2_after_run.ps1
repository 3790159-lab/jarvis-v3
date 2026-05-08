param(
    [string]$ProjectRoot = 'C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$RunsRoot = Join-Path $ProjectRoot 'jarvis_stage3_artifacts\real_runs'
$WrapperPath = Join-Path $ProjectRoot 'scripts\jarvis_sop_phase2_finalize.ps1'
$PyExe = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $PyExe)) { $PyExe = 'python' }

if (-not (Test-Path $RunsRoot)) {
    Write-Warning "Runs root not found: $RunsRoot"
    return
}
if (-not (Test-Path $WrapperPath)) {
    Write-Warning "Wrapper not found: $WrapperPath"
    return
}

$LatestRun = Get-ChildItem $RunsRoot -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like 'sop_*' } |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1

if (-not $LatestRun) {
    Write-Warning "No sop_* run folders found."
    return
}

Write-Host ""
Write-Host "== PHASE 2 FINALIZE ==" -ForegroundColor Cyan
& $WrapperPath -ProjectRoot $ProjectRoot -RunDir $LatestRun.FullName

$Phase2Report = Join-Path $LatestRun.FullName 'phase2_report.txt'
if (Test-Path $Phase2Report) {
    Write-Host ""
    Write-Host "== PHASE 2 REPORT ==" -ForegroundColor Cyan
    Get-Content $Phase2Report
}
