param(
    [string]$GatewayBaseUrl = "http://127.0.0.1:8016"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Show-Section {
    param([string]$Text)
    Write-Host ""
    Write-Host "=== $Text ===" -ForegroundColor Green
}

Show-Section "VERSION"
Invoke-RestMethod -Method GET -Uri "$GatewayBaseUrl/api/jarvis/n8n/version" -TimeoutSec 30 | ConvertTo-Json -Depth 20

Show-Section "PING"
Invoke-RestMethod -Method GET -Uri "$GatewayBaseUrl/api/jarvis/n8n/ping" -TimeoutSec 30 | ConvertTo-Json -Depth 20

Show-Section "ROUTES"
Invoke-RestMethod -Method GET -Uri "$GatewayBaseUrl/api/jarvis/n8n/_routes" -TimeoutSec 30 | ConvertTo-Json -Depth 20

Show-Section "WORKFLOWS SUMMARY"
Invoke-RestMethod -Method GET -Uri "$GatewayBaseUrl/api/jarvis/n8n/workflows/summary?limit=20" -TimeoutSec 30 | ConvertTo-Json -Depth 20

Show-Section "STATUS ALL"
Invoke-RestMethod -Method GET -Uri "$GatewayBaseUrl/api/jarvis/n8n/status/all?limit=20" -TimeoutSec 30 | ConvertTo-Json -Depth 20