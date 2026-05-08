param([string]$ProjectRoot = (Get-Location).Path)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. "$PSScriptRoot\jarvis_n8n_common.ps1"
$ctx = Get-JarvisContext -ProjectRoot $ProjectRoot

Write-Host ""
Write-Host "=== DOCKER ===" -ForegroundColor Cyan
docker info

Write-Host ""
Write-Host "=== CONTAINERS ===" -ForegroundColor Cyan
Push-Location $ctx.InfraRoot
try {
    docker compose ps
} finally {
    Pop-Location
}

if ($ctx.Env.ContainsKey("JARVIS_N8N_HEALTH_URL")) {
    Write-Host ""
    Write-Host "=== N8N HEALTH ===" -ForegroundColor Cyan
    try {
        Invoke-WebRequest -Uri $ctx.Env["JARVIS_N8N_HEALTH_URL"] -TimeoutSec 10 -UseBasicParsing |
            Select-Object StatusCode, StatusDescription
    } catch {
        Write-Host $_.Exception.Message -ForegroundColor Yellow
    }
}