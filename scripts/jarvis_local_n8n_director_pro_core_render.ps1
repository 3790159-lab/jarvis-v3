param(
    [string]$Base = "http://127.0.0.1:8024",
    [string]$TemplateId = "webhook_inbox_v5",
    [string]$Name = "",
    [string]$WebhookPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Body = @{
    template_id = $TemplateId
    name = $(if ([string]::IsNullOrWhiteSpace($Name)) { $null } else { $Name })
    webhook_path = $(if ([string]::IsNullOrWhiteSpace($WebhookPath)) { $null } else { $WebhookPath })
    activate = $false
    reuse_existing = $true
    metadata = @{}
} | ConvertTo-Json -Depth 20

Invoke-RestMethod `
    -Method POST `
    -Uri "$Base/api/jarvis/n8n/templates/render" `
    -ContentType "application/json" `
    -Body $Body `
    -TimeoutSec 120 | ConvertTo-Json -Depth 80