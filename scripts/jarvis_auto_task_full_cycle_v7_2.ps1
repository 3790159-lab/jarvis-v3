param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    [string]$BaseUrl = "http://127.0.0.1:8015",
    [int]$MaxNew = 8,
    [int]$Limit = 8
)

$ErrorActionPreference = "Continue"
Set-Location $ProjectRoot

Write-Host "=== JARVIS V7.2 AUTO TASK FULL CYCLE ===" -ForegroundColor Cyan

Write-Host ""
Write-Host "1/6 Evidence..." -ForegroundColor Yellow
& ".\scripts\jarvis_real_action_evidence_v6_3.ps1" -ProjectRoot $ProjectRoot -BaseUrl $BaseUrl

Write-Host ""
Write-Host "2/6 Gateway smoke..." -ForegroundColor Yellow
& ".\scripts\jarvis_gateway_smoke_v7_0.ps1" -ProjectRoot $ProjectRoot

Write-Host ""
Write-Host "3/6 Auto task generator..." -ForegroundColor Yellow
& ".\scripts\jarvis_auto_task_generator_v7_2.ps1" -ProjectRoot $ProjectRoot -MaxNew $MaxNew

Write-Host ""
Write-Host "4/6 Strategic brain..." -ForegroundColor Yellow
& ".\scripts\jarvis_strategic_brain_cycle_v6_4.ps1" -ProjectRoot $ProjectRoot -BaseUrl $BaseUrl

Write-Host ""
Write-Host "5/6 Gateway plan executor..." -ForegroundColor Yellow
& ".\scripts\jarvis_gateway_plan_executor_v7_2.ps1" -ProjectRoot $ProjectRoot -Limit $Limit

Write-Host ""
Write-Host "6/6 Legacy real action runner..." -ForegroundColor Yellow
& ".\scripts\jarvis_real_action_runner_v6_6.ps1" -ProjectRoot $ProjectRoot -Limit $Limit

Write-Host ""
Write-Host "=== V7.2 AUTO TASK FULL CYCLE COMPLETE ===" -ForegroundColor Green