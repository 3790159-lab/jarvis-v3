param(
    [string]$Base = "http://127.0.0.1:8024"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Invoke-RestMethod `
    -Method GET `
    -Uri "$Base/api/jarvis/n8n/workflows/inventory?limit=200" `
    -TimeoutSec 60 | ConvertTo-Json -Depth 80