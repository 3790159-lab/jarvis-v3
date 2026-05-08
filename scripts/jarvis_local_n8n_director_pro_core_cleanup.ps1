param(
    [string]$Base = "http://127.0.0.1:8024",
    [int]$OlderThanHours = 6,
    [int]$MaxKeepPerTemplate = 3,
    [switch]$Apply,
    [switch]$DeleteIfSupported
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $Apply) {
    $Uri = "$Base/api/jarvis/n8n/lifecycle/cleanup-report?older_than_hours=$OlderThanHours&max_keep_per_template=$MaxKeepPerTemplate&limit=200"
    Invoke-RestMethod -Method GET -Uri $Uri -TimeoutSec 60 | ConvertTo-Json -Depth 100
    return
}

$Body = @{
    older_than_hours = $OlderThanHours
    max_keep_per_template = $MaxKeepPerTemplate
    dry_run = $false
    deactivate_if_supported = $true
    delete_if_supported = [bool]$DeleteIfSupported
    limit = 200
} | ConvertTo-Json -Depth 20

Invoke-RestMethod `
    -Method POST `
    -Uri "$Base/api/jarvis/n8n/lifecycle/cleanup/apply" `
    -ContentType "application/json" `
    -Body $Body `
    -TimeoutSec 180 | ConvertTo-Json -Depth 120