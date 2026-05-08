param(
    [string]$BaseUrl = "http://127.0.0.1:8028",
    [string]$Focus = "reliability"
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Body = @{ focus = $Focus } | ConvertTo-Json -Depth 10
Invoke-RestMethod `
    -Method POST `
    -Uri "$BaseUrl/api/self-evolution/run/once" `
    -ContentType "application/json" `
    -Body $Body `
    -TimeoutSec 180 | ConvertTo-Json -Depth 80