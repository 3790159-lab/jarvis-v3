param(
    [string]$ProjectRoot = (Get-Location).Path
)
. "$PSScriptRoot\jarvis_n8n_common.ps1"
$ctx = Get-JarvisN8nContext -ProjectRoot $ProjectRoot

$n8nHealth = if ($ctx.ProjectEnv.ContainsKey("JARVIS_N8N_HEALTH_URL")) { $ctx.ProjectEnv["JARVIS_N8N_HEALTH_URL"] } else { "http://127.0.0.1:5678/healthz" }

Write-Host "Checking n8n health..." -ForegroundColor Cyan
$health = Invoke-WebRequest -Uri $n8nHealth -TimeoutSec 15
Write-Host ("n8n health OK: HTTP " + [int]$health.StatusCode) -ForegroundColor Green

Write-Host "Sending production webhook smoke payload..." -ForegroundColor Cyan
try {
    & (Join-Path $PSScriptRoot "jarvis_n8n_dispatch.ps1") `
        -ProjectRoot $ProjectRoot `
        -Action "echo" `
        -Intent "smoke_test" `
        -MissionId ("smoke-" + [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()) `
        -TaskId "echo-1" `
        -PayloadJson '{"message":"jarvis smoke test"}'
} catch {
    Write-Host ""
    Write-Host "Dispatch failed." -ForegroundColor Yellow
    Write-Host "Если здесь 404, значит workflow /webhook/jarvis/inbox ещё не опубликован в n8n." -ForegroundColor Yellow
    Write-Host "Сначала создай и publish workflow с Webhook path = jarvis/inbox." -ForegroundColor Yellow
    throw
}