param(
    [string]$Base = "http://127.0.0.1:8020",
    [string]$TemplateId = "webhook_inbox_v1",
    [string]$Name = "",
    [string]$WebhookPath = "",
    [switch]$Activate
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Body = @{
    template_id = $TemplateId
    name = $(if ([string]::IsNullOrWhiteSpace($Name)) { $null } else { $Name })
    webhook_path = $(if ([string]::IsNullOrWhiteSpace($WebhookPath)) { $null } else { $WebhookPath })
    activate = [bool]$Activate
} | ConvertTo-Json -Depth 10

$Uri = if ($Activate) { "$Base/api/jarvis/n8n/templates/create-and-activate" } else { "$Base/api/jarvis/n8n/templates/create" }

Invoke-RestMethod `
    -Method POST `
    -Uri $Uri `
    -ContentType "application/json" `
    -Body $Body `
    -TimeoutSec 120 | ConvertTo-Json -Depth 40