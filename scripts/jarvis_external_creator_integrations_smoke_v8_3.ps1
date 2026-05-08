param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    [int]$Limit = 5
)

$ErrorActionPreference = "Continue"
Set-Location $ProjectRoot

Write-Host "=== EXTERNAL CREATOR INTEGRATIONS V8.3 SMOKE ===" -ForegroundColor Cyan

.\scripts\jarvis_external_creator_integrations_v8_3.ps1 -ProjectRoot $ProjectRoot -AddQueue

Write-Host ""
Write-Host "=== EXECUTE INTEGRATION QUEUE TASKS ===" -ForegroundColor Cyan
.\scripts\jarvis_gateway_plan_executor_v7_2.ps1 -ProjectRoot $ProjectRoot -Limit $Limit