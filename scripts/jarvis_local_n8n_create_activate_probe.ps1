param(
    [string]$GatewayBaseUrl = "http://127.0.0.1:8016",
    [string]$Name = "",
    [string]$WebhookPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Show-Section {
    param([string]$Text)
    Write-Host ""
    Write-Host "=== $Text ===" -ForegroundColor Green
}

$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
if ([string]::IsNullOrWhiteSpace($Name)) { $Name = "jarvis-manual-$Stamp" }
if ([string]::IsNullOrWhiteSpace($WebhookPath)) { $WebhookPath = "jarvis-manual-$Stamp" }

Show-Section "CREATE"
$CreateBody = @{
    name = $Name
    webhook_path = $WebhookPath
} | ConvertTo-Json -Depth 10

$Create = Invoke-RestMethod `
    -Method POST `
    -Uri "$GatewayBaseUrl/api/jarvis/n8n/workflows/create-webhook" `
    -ContentType "application/json" `
    -Body $CreateBody `
    -TimeoutSec 60
$Create | ConvertTo-Json -Depth 20

$WorkflowId = $Create.body.workflow_id
if (-not $WorkflowId) { throw "workflow_id missing" }

Show-Section "ACTIVATE"
$Activate = Invoke-RestMethod `
    -Method POST `
    -Uri "$GatewayBaseUrl/api/jarvis/n8n/workflows/activate/$WorkflowId" `
    -TimeoutSec 60
$Activate | ConvertTo-Json -Depth 20

Start-Sleep -Seconds 4

Show-Section "PROBE"
$ProbeBody = @{
    payload = @{
        source = "jarvis_local_n8n_helper"
        message = "hello from helper"
        ts = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    }
} | ConvertTo-Json -Depth 10

$Probe = Invoke-RestMethod `
    -Method POST `
    -Uri "$GatewayBaseUrl/api/jarvis/n8n/probe/$WebhookPath" `
    -ContentType "application/json" `
    -Body $ProbeBody `
    -TimeoutSec 60
$Probe | ConvertTo-Json -Depth 20