param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    [string]$BaseUrl = "http://127.0.0.1:8015",
    [switch]$RunSession
)

Set-ExecutionPolicy -Scope Process Bypass -Force
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Set-Location $ProjectRoot

Write-Host "=== UNIFIED NIGHT API HEALTH ===" -ForegroundColor Cyan
$health = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/unified-night/health" -TimeoutSec 20
$health | ConvertTo-Json -Depth 10

Write-Host ""
Write-Host "=== UNIFIED NIGHT API METRICS ===" -ForegroundColor Cyan
$metrics = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/unified-night/metrics" -TimeoutSec 20
$metrics | ConvertTo-Json -Depth 10

if ($RunSession) {
    Write-Host ""
    Write-Host "=== RUN UNIFIED NIGHT SESSION VIA API ===" -ForegroundColor Cyan
    $body = @{
        max_iterations = 1
        degrade_on_failure = $true
        mode = "api_safe_unified_night_mode"
    } | ConvertTo-Json -Depth 10

    $result = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/unified-night/run" -ContentType "application/json" -Body $body -TimeoutSec 180
    $result | ConvertTo-Json -Depth 20
}

Write-Host ""
Write-Host "UNIFIED NIGHT API SMOKE OK" -ForegroundColor Green