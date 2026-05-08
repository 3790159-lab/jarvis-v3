param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    [string]$BaseUrl = "http://127.0.0.1:8015",
    [int]$Limit = 8
)

$ErrorActionPreference = "Continue"
Set-Location $ProjectRoot

Write-Host "=== JARVIS NIGHT SELF-HEALING CYCLE V8.2 ===" -ForegroundColor Cyan

Write-Host "1/5 Backend self-healing"
& ".\scripts\jarvis_backend_self_healing_v8_2.ps1" -ProjectRoot $ProjectRoot -BaseUrl $BaseUrl

Write-Host "2/5 Evidence"
& ".\scripts\jarvis_real_action_evidence_v6_3.ps1" -ProjectRoot $ProjectRoot -BaseUrl $BaseUrl

Write-Host "3/5 Auto task generator"
& ".\scripts\jarvis_auto_task_generator_v7_2.ps1" -ProjectRoot $ProjectRoot -MaxNew 8

Write-Host "4/5 Strategic brain"
& ".\scripts\jarvis_strategic_brain_cycle_v6_4.ps1" -ProjectRoot $ProjectRoot -BaseUrl $BaseUrl

Write-Host "5/5 Execute plans"
& ".\scripts\jarvis_gateway_plan_executor_v7_2.ps1" -ProjectRoot $ProjectRoot -Limit $Limit