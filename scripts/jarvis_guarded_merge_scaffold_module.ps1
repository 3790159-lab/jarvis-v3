param(
    [string]$BaseUrl = "http://127.0.0.1:8029",
    [Parameter(Mandatory = $true)][string]$BlueprintId,
    [Parameter(Mandatory = $true)][string]$ModuleName,
    [string]$DisplayName = "",
    [string]$Purpose = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Body = @{
    blueprint_id = $BlueprintId
    module_name  = $ModuleName
    display_name = $(if ($DisplayName) { $DisplayName } else { $null })
    purpose      = $(if ($Purpose) { $Purpose } else { $null })
} | ConvertTo-Json -Depth 20

Invoke-RestMethod `
    -Method POST `
    -Uri "$BaseUrl/api/guarded-merge/modules/scaffold" `
    -ContentType "application/json" `
    -Body $Body `
    -TimeoutSec 120 | ConvertTo-Json -Depth 100