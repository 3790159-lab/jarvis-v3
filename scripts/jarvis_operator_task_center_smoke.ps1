param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    [string]$BaseUrl = "http://127.0.0.1:8015",
    [switch]$SendTelegram
)

Set-ExecutionPolicy -Scope Process Bypass -Force
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Set-Location $ProjectRoot

Write-Host "=== OPERATOR HEALTH ===" -ForegroundColor Cyan
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/operator/health" -TimeoutSec 20 | ConvertTo-Json -Depth 10

Write-Host ""
Write-Host "=== CREATE PARALLEL TASK ===" -ForegroundColor Cyan
$body = @{
    title = "Parallel test task"
    objective = "Check that operator task center can track parallel tasks"
    priority = "normal"
    plan = @(
        "Register task",
        "Track status",
        "Ask Jarvis next action"
    )
} | ConvertTo-Json -Depth 10

$created = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/operator/tasks" -ContentType "application/json" -Body $body -TimeoutSec 20
$created | ConvertTo-Json -Depth 10

Write-Host ""
Write-Host "=== OPERATOR STATUS ===" -ForegroundColor Cyan
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/operator/status" -TimeoutSec 20 | ConvertTo-Json -Depth 20

Write-Host ""
Write-Host "=== OPERATOR NEXT ===" -ForegroundColor Cyan
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/operator/next" -TimeoutSec 20 | ConvertTo-Json -Depth 20

if ($SendTelegram) {
    Write-Host ""
    Write-Host "=== SEND TELEGRAM REPORT ===" -ForegroundColor Cyan
    Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/operator/telegram/report" -TimeoutSec 30 | ConvertTo-Json -Depth 20
}

Write-Host ""
Write-Host "OPERATOR TASK CENTER SMOKE OK" -ForegroundColor Green