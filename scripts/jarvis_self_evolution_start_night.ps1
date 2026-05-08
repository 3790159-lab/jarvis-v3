param(
    [string]$BaseUrl = "http://127.0.0.1:8028",
    [string]$Focus = "reliability",
    [double]$Hours = 8
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Body = @{
    focus = $Focus
    hours = $Hours
    mode  = "night"
} | ConvertTo-Json -Depth 10

Invoke-RestMethod `
    -Method POST `
    -Uri "$BaseUrl/api/self-evolution/runner/start" `
    -ContentType "application/json" `
    -Body $Body `
    -TimeoutSec 60 | ConvertTo-Json -Depth 40