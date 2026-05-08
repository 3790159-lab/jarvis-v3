param(
    [string]$Base = "http://127.0.0.1:8024",
    [string]$Goal = "Need an intake webhook for Jarvis events",
    [string]$Purpose = "",
    [string]$Tag = "",
    [switch]$Create,
    [switch]$DoNotActivate,
    [string]$Name = "",
    [string]$WebhookPath = "",
    [switch]$NoReuse
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $Create) {
    $Uri = "$Base/api/jarvis/n8n/planner/recommend?goal=$([uri]::EscapeDataString($Goal))&purpose=$([uri]::EscapeDataString($Purpose))&tag=$([uri]::EscapeDataString($Tag))"
    Invoke-RestMethod -Method GET -Uri $Uri -TimeoutSec 60 | ConvertTo-Json -Depth 80
    return
}

$Body = @{
    goal = $Goal
    purpose = $Purpose
    tag = $Tag
    name = $(if ([string]::IsNullOrWhiteSpace($Name)) { $null } else { $Name })
    webhook_path = $(if ([string]::IsNullOrWhiteSpace($WebhookPath)) { $null } else { $WebhookPath })
    activate = (-not [bool]$DoNotActivate)
    reuse_existing = (-not [bool]$NoReuse)
    metadata = @{}
} | ConvertTo-Json -Depth 20

Invoke-RestMethod `
    -Method POST `
    -Uri "$Base/api/jarvis/n8n/planner/create-from-goal" `
    -ContentType "application/json" `
    -Body $Body `
    -TimeoutSec 180 | ConvertTo-Json -Depth 100