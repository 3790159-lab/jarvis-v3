param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    [string]$BaseUrl = "http://127.0.0.1:8015",
    [int]$Limit = 3
)

$ErrorActionPreference = "Continue"
Set-Location $ProjectRoot

Write-Host "=== JARVIS FULL SAFE CYCLE V6.5 ===" -ForegroundColor Cyan

Write-Host ""
Write-Host "1/3 Evidence..." -ForegroundColor Yellow
& ".\scripts\jarvis_real_action_evidence_v6_3.ps1" -ProjectRoot $ProjectRoot -BaseUrl $BaseUrl

Write-Host ""
Write-Host "2/3 Strategic brain..." -ForegroundColor Yellow
& ".\scripts\jarvis_strategic_brain_cycle_v6_4.ps1" -ProjectRoot $ProjectRoot -BaseUrl $BaseUrl

Write-Host ""
Write-Host "3/3 Queue executor..." -ForegroundColor Yellow
& ".\scripts\jarvis_queue_executor_v6_5.ps1" -ProjectRoot $ProjectRoot -Limit $Limit

Write-Host ""
Write-Host "=== FULL SAFE CYCLE COMPLETED ===" -ForegroundColor Green