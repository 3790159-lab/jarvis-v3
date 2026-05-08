param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

Set-ExecutionPolicy -Scope Process Bypass -Force
Set-Location $ProjectRoot

Write-Host "=== CLAUDE ECOSYSTEM V1.2 SMOKE ===" -ForegroundColor Cyan

Write-Host ""
Write-Host "1) /api/claude-ecosystem/health" -ForegroundColor Yellow
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/claude-ecosystem/health" -TimeoutSec 10 | ConvertTo-Json -Depth 20

Write-Host ""
Write-Host "2) /api/claude-ecosystem/registry" -ForegroundColor Yellow
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/claude-ecosystem/registry" -TimeoutSec 10 | ConvertTo-Json -Depth 20

Write-Host ""
Write-Host "3) /api/claude-ecosystem/preflight" -ForegroundColor Yellow
$Body = @{
    source = "smoke"
    checks = @("compile", "health", "root_endpoint", "night_api")
    goal = "verify claude ecosystem integration"
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/claude-ecosystem/preflight" -ContentType "application/json" -Body $Body -TimeoutSec 20 | ConvertTo-Json -Depth 30

Write-Host ""
Write-Host "4) /api/claude-ecosystem/night-mode/before" -ForegroundColor Yellow
$NightBody = @{
    source = "smoke"
    mode = "safe_package_first"
    note = "night mode must use hooks, QA, and recovery checks"
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/claude-ecosystem/night-mode/before" -ContentType "application/json" -Body $NightBody -TimeoutSec 20 | ConvertTo-Json -Depth 30

Write-Host ""
Write-Host "=== DONE ===" -ForegroundColor Green