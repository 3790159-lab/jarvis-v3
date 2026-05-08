param([string]$BaseUrl = "http://127.0.0.1:8029")
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/guarded-merge/modules" -TimeoutSec 30 | ConvertTo-Json -Depth 100