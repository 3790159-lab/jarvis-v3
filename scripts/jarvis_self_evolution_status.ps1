param([string]$BaseUrl = "http://127.0.0.1:8028")
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Health = Invoke-RestMethod -Method GET -Uri "$BaseUrl/health" -TimeoutSec 20
$Status = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/self-evolution/status" -TimeoutSec 30
$Config = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/self-evolution/config" -TimeoutSec 30

Write-Host ""
Write-Host "=== SELF EVOLUTION HEALTH ===" -ForegroundColor Green
$Health | ConvertTo-Json -Depth 30

Write-Host ""
Write-Host "=== SELF EVOLUTION STATUS ===" -ForegroundColor Green
$Status | ConvertTo-Json -Depth 80

Write-Host ""
Write-Host "=== SELF EVOLUTION CONFIG ===" -ForegroundColor Green
$Config | ConvertTo-Json -Depth 80