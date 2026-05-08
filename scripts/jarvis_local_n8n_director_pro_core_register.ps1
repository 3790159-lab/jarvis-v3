param(
    [string]$Base = "http://127.0.0.1:8024",
    [string]$TemplateId = "custom_hook_v2",
    [string]$Purpose = "custom"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Body = @{
    id = $TemplateId
    title = "Custom Hook v2"
    description = "Custom user-registered webhook template"
    purpose = $Purpose
    tags = @("custom", "webhook", "learning")
    path_prefix = $TemplateId
    name_prefix = $TemplateId
    smoke_payload = @{
        source = $TemplateId
        message = "hello from registered custom template"
    }
} | ConvertTo-Json -Depth 20

Invoke-RestMethod `
    -Method POST `
    -Uri "$Base/api/jarvis/n8n/templates/register" `
    -ContentType "application/json" `
    -Body $Body `
    -TimeoutSec 60 | ConvertTo-Json -Depth 80