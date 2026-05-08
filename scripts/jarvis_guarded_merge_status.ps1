param([string]$BaseUrl = "http://127.0.0.1:8029")
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Health = Invoke-RestMethod -Method GET -Uri "$BaseUrl/health" -TimeoutSec 20
$Status = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/guarded-merge/status" -TimeoutSec 30

Write-Host ""
Write-Host "=== GUARDED MERGE HEALTH ===" -ForegroundColor Green
$Health | ConvertTo-Json -Depth 30

Write-Host ""
Write-Host "=== GUARDED MERGE STATUS ===" -ForegroundColor Green
$Status | ConvertTo-Json -Depth 100