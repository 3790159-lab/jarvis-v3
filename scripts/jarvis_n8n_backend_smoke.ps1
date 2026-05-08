param(
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "=== BACKEND HEALTH ===" -ForegroundColor Cyan
Invoke-RestMethod -Method GET -Uri "$BaseUrl/health" -TimeoutSec 10 | ConvertTo-Json -Depth 20

Write-Host ""
Write-Host "=== N8N BRIDGE HEALTH ===" -ForegroundColor Cyan
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/n8n/health" -TimeoutSec 10 | ConvertTo-Json -Depth 20

Write-Host ""
Write-Host "=== BACKEND -> N8N DISPATCH ===" -ForegroundColor Cyan
$Body = @{
    action = "echo"
    intent = "backend_route_smoke"
    mission_id = "backend-smoke-" + [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    task_id = "dispatch-1"
    source = "jarvis"
    use_test_webhook = $false
    payload = @{
        message = "hello from backend route"
        test = $true
    }
} | ConvertTo-Json -Depth 20

Invoke-RestMethod `
    -Method POST `
    -Uri "$BaseUrl/api/n8n/dispatch" `
    -ContentType "application/json" `
    -Body $Body | ConvertTo-Json -Depth 20