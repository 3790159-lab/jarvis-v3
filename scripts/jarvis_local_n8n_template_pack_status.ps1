param(
    [string]$Base = "http://127.0.0.1:8020"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Show-Section {
    param([string]$Text)
    Write-Host ""
    Write-Host "=== $Text ===" -ForegroundColor Green
}

Show-Section "WHOAMI"
Invoke-RestMethod -Method GET -Uri "$Base/__whoami" -TimeoutSec 30 | ConvertTo-Json -Depth 20

Show-Section "VERSION"
Invoke-RestMethod -Method GET -Uri "$Base/api/jarvis/n8n/version" -TimeoutSec 30 | ConvertTo-Json -Depth 20

Show-Section "STATUS ALL"
Invoke-RestMethod -Method GET -Uri "$Base/api/jarvis/n8n/status/all?limit=20" -TimeoutSec 30 | ConvertTo-Json -Depth 20

Show-Section "WORKFLOWS SUMMARY"
Invoke-RestMethod -Method GET -Uri "$Base/api/jarvis/n8n/workflows/summary?limit=20" -TimeoutSec 30 | ConvertTo-Json -Depth 20

Show-Section "TEMPLATES"
Invoke-RestMethod -Method GET -Uri "$Base/api/jarvis/n8n/templates" -TimeoutSec 30 | ConvertTo-Json -Depth 20