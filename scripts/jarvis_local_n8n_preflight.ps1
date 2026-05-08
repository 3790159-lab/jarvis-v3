param(
    [string]$GatewayBaseUrl = "http://127.0.0.1:8016",
    [string]$BridgeBaseUrl = "http://127.0.0.1:8030",
    [string]$EditorBaseUrl = "http://127.0.0.1:5680",
    [string]$WorkerHealthUrl = "http://127.0.0.1:5681/healthz"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Show-Section {
    param([string]$Text)
    Write-Host ""
    Write-Host "=== $Text ===" -ForegroundColor Green
}

function Try-Json {
    param([string]$Uri)
    try {
        return @{ ok = $true; body = (Invoke-RestMethod -Method GET -Uri $Uri -TimeoutSec 30) }
    } catch {
        return @{ ok = $false; error = $_.Exception.Message }
    }
}

Show-Section "EDITOR READINESS"
Try-Json "$EditorBaseUrl/healthz/readiness" | ConvertTo-Json -Depth 20

Show-Section "WORKER HEALTH"
Try-Json $WorkerHealthUrl | ConvertTo-Json -Depth 20

Show-Section "BRIDGE HEALTH"
Try-Json "$BridgeBaseUrl/health" | ConvertTo-Json -Depth 20

Show-Section "GATEWAY VERSION"
Try-Json "$GatewayBaseUrl/api/jarvis/n8n/version" | ConvertTo-Json -Depth 20

Show-Section "STATUS ALL"
Try-Json "$GatewayBaseUrl/api/jarvis/n8n/status/all?limit=20" | ConvertTo-Json -Depth 20