param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    [string]$BaseUrl = "http://127.0.0.1:8015",
    [int]$Limit = 5
)

$ErrorActionPreference = "Continue"
Set-Location $ProjectRoot

Write-Host "=== JARVIS REAL FULL CYCLE V6.6 ===" -ForegroundColor Cyan

Write-Host ""
Write-Host "1/4 Evidence..." -ForegroundColor Yellow
& ".\scripts\jarvis_real_action_evidence_v6_3.ps1" -ProjectRoot $ProjectRoot -BaseUrl $BaseUrl

Write-Host ""
Write-Host "2/4 Strategic brain..." -ForegroundColor Yellow
& ".\scripts\jarvis_strategic_brain_cycle_v6_4.ps1" -ProjectRoot $ProjectRoot -BaseUrl $BaseUrl

Write-Host ""
Write-Host "3/4 Queue executor..." -ForegroundColor Yellow
& ".\scripts\jarvis_queue_executor_v6_5.ps1" -ProjectRoot $ProjectRoot -Limit $Limit

Write-Host ""
Write-Host "4/4 Real action runner..." -ForegroundColor Yellow
& ".\scripts\jarvis_real_action_runner_v6_6.ps1" -ProjectRoot $ProjectRoot -Limit $Limit

Write-Host ""
Write-Host "=== REAL FULL CYCLE V6.6 COMPLETED ===" -ForegroundColor Green