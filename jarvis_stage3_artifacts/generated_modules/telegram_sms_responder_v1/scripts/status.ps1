param([string]$BaseUrl = "http://127.0.0.1:8110")

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Health = Invoke-RestMethod -Method GET -Uri "$BaseUrl/health" -TimeoutSec 30
$Config = Invoke-RestMethod -Method GET -Uri "$BaseUrl/config" -TimeoutSec 30

Write-Host ""
Write-Host "=== TELEGRAM MODULE HEALTH ===" -ForegroundColor Green
$Health | ConvertTo-Json -Depth 30

Write-Host ""
Write-Host "=== TELEGRAM MODULE CONFIG ===" -ForegroundColor Green
$Config | ConvertTo-Json -Depth 40