param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

Set-ExecutionPolicy -Scope Process Bypass -Force
Set-Location $ProjectRoot

Write-Host "=== CLAUDE ECOSYSTEM V1.3 MEGA SMOKE ===" -ForegroundColor Cyan

Write-Host ""
Write-Host "1) health" -ForegroundColor Yellow
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/claude-ecosystem/health" -TimeoutSec 10 | ConvertTo-Json -Depth 30

Write-Host ""
Write-Host "2) ultra plan" -ForegroundColor Yellow
$UltraBody = @{
    source = "v1_3_mega_smoke"
    goal = "verify ultra upgrade engine"
    mode = "safe_package_first"
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/claude-ecosystem/ultra/plan" -ContentType "application/json" -Body $UltraBody -TimeoutSec 20 | ConvertTo-Json -Depth 40

Write-Host ""
Write-Host "3) full creator package" -ForegroundColor Yellow
$CreatorBody = @{
    source = "v1_3_mega_smoke"
    product_name = "Jarvis Operator Command Center"
    goal = "Create operator dashboard concept for missions, Night Mode, artifacts, and approvals"
    target_user = "Jarvis operator"
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/claude-ecosystem/full-creator/package" -ContentType "application/json" -Body $CreatorBody -TimeoutSec 20 | ConvertTo-Json -Depth 40

Write-Host ""
Write-Host "4) safe night preflight" -ForegroundColor Yellow
$NightBody = @{
    source = "v1_3_mega_smoke"
    run_type = "night_mode"
    policy = "package_first"
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/claude-ecosystem/safe-night/preflight" -ContentType "application/json" -Body $NightBody -TimeoutSec 20 | ConvertTo-Json -Depth 40

Write-Host ""
Write-Host "=== V1.3 MEGA SMOKE DONE ===" -ForegroundColor Green