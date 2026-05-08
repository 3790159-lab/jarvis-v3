param([string]$BaseUrl = "http://127.0.0.1:8029")
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/guarded-merge/blueprints/register-defaults" -TimeoutSec 30 | ConvertTo-Json -Depth 100