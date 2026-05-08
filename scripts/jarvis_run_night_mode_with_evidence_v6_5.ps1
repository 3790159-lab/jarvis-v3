param(
    [int]$DurationHours = 6,
    [int]$SleepSeconds = 90,
    [int]$MaxIterations = 20,
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

$ErrorActionPreference = "Continue"
Set-Location $ProjectRoot

Write-Host "=== PRE-NIGHT EVIDENCE GATE ===" -ForegroundColor Cyan
& ".\scripts\jarvis_truth_guard_evidence_bridge_v6_3.ps1" -ProjectRoot $ProjectRoot -BaseUrl $BaseUrl
$EvidenceExit = $LASTEXITCODE

if ($EvidenceExit -ne 0) {
    Write-Host "Evidence gate did not fully pass. Night mode can continue only in safe review mode." -ForegroundColor Yellow
}

$Night = ".\scripts\jarvis_night_mode_v6_2_brain_loop_safe.ps1"
if (-not (Test-Path $Night)) {
    throw "Night mode script not found: $Night"
}

Write-Host "=== START NIGHT MODE WITH EVIDENCE AWARENESS ===" -ForegroundColor Green
& $Night -DurationHours $DurationHours -SleepSeconds $SleepSeconds -MaxIterations $MaxIterations
