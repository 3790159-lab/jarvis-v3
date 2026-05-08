param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram"
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectRoot

Write-Host "=== V7.6 TOOL SELECTION SMOKE ===" -ForegroundColor Cyan

.\scripts\jarvis_select_tools_v7_6.ps1 `
    -ProjectRoot $ProjectRoot `
    -Task "Check backend health, routes and recent errors" `
    -DryRun

Write-Host ""
Write-Host "=== EXECUTE SELECTED PLAN ===" -ForegroundColor Cyan

.\scripts\jarvis_gateway_plan_executor_v7_2.ps1 `
    -ProjectRoot $ProjectRoot `
    -Limit 3