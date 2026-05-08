param(
    [string]$BackendBaseUrl = "http://127.0.0.1:8015"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host "=== HEALTH ===" -ForegroundColor Green
$health = Invoke-RestMethod -Method GET -Uri "$BackendBaseUrl/health" -TimeoutSec 10
$health | ConvertTo-Json -Depth 10

Write-Host ""
Write-Host "=== N8N MATERIALIZER HEALTH ===" -ForegroundColor Green
$n8nHealth = Invoke-RestMethod -Method GET -Uri "$BackendBaseUrl/api/n8n/materializer/health" -TimeoutSec 10
$n8nHealth | ConvertTo-Json -Depth 10

$suffix = "{0:yyyyMMddHHmmss}-{1}" -f (Get-Date), (Get-Random -Minimum 1000 -Maximum 9999)

$body = @{
    name         = "jarvis-router-$suffix"
    webhook_path = "jarvis-router-$suffix"
    response_text= "Jarvis n8n workflow is alive"
    probe_payload = @{
        source = "jarvis_n8n_action_router_smoke"
        ping   = "pong"
        ts     = (Get-Date).ToString("o")
    }
} | ConvertTo-Json -Depth 20

Write-Host ""
Write-Host "=== PUBLISH AND PROBE ===" -ForegroundColor Green
$result = Invoke-RestMethod `
    -Method POST `
    -Uri "$BackendBaseUrl/api/n8n/materializer/publish-and-probe" `
    -ContentType "application/json" `
    -Body $body `
    -TimeoutSec 90

$result | ConvertTo-Json -Depth 30

$probeStatus = $result.result.probe.status_code
if ($probeStatus -lt 200 -or $probeStatus -ge 300) {
    throw "Probe failed with status code: $probeStatus"
}

Write-Host ""
Write-Host "Smoke test completed successfully." -ForegroundColor Green