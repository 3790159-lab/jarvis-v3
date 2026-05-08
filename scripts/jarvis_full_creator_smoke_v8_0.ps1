param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram"
)

$ErrorActionPreference = "Continue"
Set-Location $ProjectRoot

Write-Host "=== FULL CREATOR V8.0 SMOKE ===" -ForegroundColor Cyan

.\scripts\jarvis_queue_guard_v7_7.ps1 -ProjectRoot $ProjectRoot -Restore

.\scripts\jarvis_full_creator_v8_0.ps1 `
    -ProjectRoot $ProjectRoot `
    -Idea "Local AI Operator Dashboard for Jarvis autonomy, n8n workflows, evidence reports and night-mode improvements." `
    -AddQueue

.\scripts\jarvis_gateway_plan_executor_v7_2.ps1 `
    -ProjectRoot $ProjectRoot `
    -Limit 3