param(
    [string]$Base = "http://127.0.0.1:8020",
    [string]$BridgeBase = "http://127.0.0.1:8030",
    [string]$EditorBase = "http://127.0.0.1:5680",
    [string]$WorkerHealthUrl = "http://127.0.0.1:5681/healthz"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Show-Section {
    param([string]$Text)
    Write-Host ""
    Write-Host "=== $Text ===" -ForegroundColor Green
}

function Try-Json {
    param([string]$Uri)
    try {
        return @{ ok = $true; body = (Invoke-RestMethod -Method GET -Uri $Uri -TimeoutSec 30) }
    } catch {
        return @{ ok = $false; error = $_.Exception.Message }
    }
}

Show-Section "EDITOR READINESS"
Try-Json "$EditorBase/healthz/readiness" | ConvertTo-Json -Depth 20

Show-Section "WORKER HEALTH"
Try-Json $WorkerHealthUrl | ConvertTo-Json -Depth 20

Show-Section "BRIDGE HEALTH"
Try-Json "$BridgeBase/health" | ConvertTo-Json -Depth 20

Show-Section "WHOAMI"
Try-Json "$Base/__whoami" | ConvertTo-Json -Depth 20

Show-Section "STATUS ALL"
Try-Json "$Base/api/jarvis/n8n/status/all?limit=20" | ConvertTo-Json -Depth 20

Show-Section "TEMPLATES"
Try-Json "$Base/api/jarvis/n8n/templates" | ConvertTo-Json -Depth 20