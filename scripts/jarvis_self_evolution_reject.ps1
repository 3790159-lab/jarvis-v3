param(
    [Parameter(Mandatory = $true)][string]$ProposalId,
    [string]$BaseUrl = "http://127.0.0.1:8028"
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Invoke-RestMethod `
    -Method POST `
    -Uri "$BaseUrl/api/self-evolution/proposals/$ProposalId/reject" `
    -TimeoutSec 60 | ConvertTo-Json -Depth 80