param(
    [string]$ProjectRoot = (Get-Location).Path
)
. "$PSScriptRoot\jarvis_n8n_common.ps1"
$ctx = Get-JarvisN8nContext -ProjectRoot $ProjectRoot

$jarvisBase = if ($ctx.ProjectEnv.ContainsKey("JARVIS_BASE_URL")) { $ctx.ProjectEnv["JARVIS_BASE_URL"] } else { "http://127.0.0.1:8015" }
$n8nBase = if ($ctx.ProjectEnv.ContainsKey("JARVIS_N8N_BASE_URL")) { $ctx.ProjectEnv["JARVIS_N8N_BASE_URL"] } else { "http://127.0.0.1:5678" }
$n8nHealth = if ($ctx.ProjectEnv.ContainsKey("JARVIS_N8N_HEALTH_URL")) { $ctx.ProjectEnv["JARVIS_N8N_HEALTH_URL"] } else { "$n8nBase/healthz" }

Write-Host ""
Write-Host "=== DOCKER ===" -ForegroundColor Cyan
& $ctx.DockerCli version

Write-Host ""
Write-Host "=== N8N CONTAINERS ===" -ForegroundColor Cyan
Push-Location $ctx.N8nRoot
try {
    & $ctx.DockerCli compose ps
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "=== N8N HEALTH ===" -ForegroundColor Cyan
try {
    $resp = Invoke-WebRequest -Uri $n8nHealth -TimeoutSec 10
    Write-Host ("n8n health: HTTP " + [int]$resp.StatusCode) -ForegroundColor Green
} catch {
    Write-Host ("n8n health failed: " + $_.Exception.Message) -ForegroundColor Red
}

Write-Host ""
Write-Host "=== JARVIS HEALTH ===" -ForegroundColor Cyan
try {
    $jarvisHealthUrl = "$jarvisBase/health"
    $resp = Invoke-RestMethod -Method GET -Uri $jarvisHealthUrl -TimeoutSec 10
    $resp | ConvertTo-Json -Depth 10
} catch {
    Write-Host ("jarvis health failed: " + $_.Exception.Message) -ForegroundColor Yellow
}