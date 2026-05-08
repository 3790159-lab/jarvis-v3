param(
    [string]$ProjectRoot = 'C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$PyExe = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $PyExe)) { $PyExe = 'python' }

$FinalizePy = Join-Path $ProjectRoot 'scripts\jarvis_sop_phase2_finalize.py'
$NormalizePy = Join-Path $ProjectRoot 'scripts\jarvis_artifact_normalize_utf8.py'
$RunsRoot = Join-Path $ProjectRoot 'jarvis_stage3_artifacts\real_runs'

& $PyExe $FinalizePy $ProjectRoot
if ($LASTEXITCODE -ne 0) {
    throw "Finalize python returned exit code $LASTEXITCODE"
}

$LatestRun = Get-ChildItem $RunsRoot -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like 'sop_*' } |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1

if ($LatestRun) {
    & $PyExe $NormalizePy $LatestRun.FullName | Out-Host

    Write-Host ""
    # HARDENING_MARKER_NORMALIZE_RUN_DIR
try {
    if (Get-Command Normalize-ArtifactRunDir -ErrorAction SilentlyContinue) {
        [void](Normalize-ArtifactRunDir -ProjectRoot $ProjectRoot -RunDir $RunDir)
    }
}
catch {
    Write-Warning ("Artifact normalization warning: {0}" -f $_.Exception.Message)
}
Write-Host "== PHASE 2 ARTIFACT CHECK ==" -ForegroundColor Cyan
    Get-ChildItem $LatestRun.FullName |
        Select-Object Name, Length, LastWriteTime |
        Format-Table -AutoSize
}
