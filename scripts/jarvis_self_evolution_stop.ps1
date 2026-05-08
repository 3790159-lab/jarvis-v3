param([string]$BaseUrl = "http://127.0.0.1:8028")
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Invoke-RestMethod `
    -Method POST `
    -Uri "$BaseUrl/api/self-evolution/runner/stop" `
    -TimeoutSec 30 | ConvertTo-Json -Depth 40