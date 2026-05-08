param(
    [string]$Base = "http://127.0.0.1:8024"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Show-Section {
    param([string]$Text)
    Write-Host ""
    Write-Host "=== $Text ===" -ForegroundColor Green
}

Show-Section "WHOAMI"
Invoke-RestMethod -Method GET -Uri "$Base/__whoami" -TimeoutSec 30 | ConvertTo-Json -Depth 80

Show-Section "ABILITIES"
Invoke-RestMethod -Method GET -Uri "$Base/api/jarvis/n8n/abilities" -TimeoutSec 30 | ConvertTo-Json -Depth 80

Show-Section "STATUS ALL"
Invoke-RestMethod -Method GET -Uri "$Base/api/jarvis/n8n/status/all?limit=200" -TimeoutSec 60 | ConvertTo-Json -Depth 80

Show-Section "INVENTORY"
Invoke-RestMethod -Method GET -Uri "$Base/api/jarvis/n8n/workflows/inventory?limit=200" -TimeoutSec 60 | ConvertTo-Json -Depth 80

Show-Section "CLEANUP REPORT"
Invoke-RestMethod -Method GET -Uri "$Base/api/jarvis/n8n/lifecycle/cleanup-report?older_than_hours=6&max_keep_per_template=3&limit=200" -TimeoutSec 60 | ConvertTo-Json -Depth 80

Show-Section "MEMORY"
Invoke-RestMethod -Method GET -Uri "$Base/api/jarvis/n8n/memory" -TimeoutSec 60 | ConvertTo-Json -Depth 80