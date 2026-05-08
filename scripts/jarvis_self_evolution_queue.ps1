param([string]$BaseUrl = "http://127.0.0.1:8028")
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Invoke-RestMethod `
    -Method GET `
    -Uri "$BaseUrl/api/self-evolution/proposals" `
    -TimeoutSec 30 | ConvertTo-Json -Depth 100