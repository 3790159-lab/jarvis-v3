param([string]$BaseUrl = "http://127.0.0.1:8029")
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/guarded-merge/package-all-approved" -TimeoutSec 120 | ConvertTo-Json -Depth 100