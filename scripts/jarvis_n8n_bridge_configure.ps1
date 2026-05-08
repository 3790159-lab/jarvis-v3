param(
    [string]$BridgeBaseUrl = "http://127.0.0.1:8030"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ApiKey = Read-Host "Paste your SELF-HOSTED n8n API key"
if ([string]::IsNullOrWhiteSpace($ApiKey)) {
    throw "API key was empty."
}

$Body = @{
    api_key = $ApiKey
} | ConvertTo-Json -Depth 10

Invoke-RestMethod `
    -Method POST `
    -Uri "$BridgeBaseUrl/api/configure" `
    -ContentType "application/json" `
    -Body $Body

Invoke-RestMethod `
    -Method GET `
    -Uri "$BridgeBaseUrl/api/n8n/public-api-check"