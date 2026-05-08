param(
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

function Show-Json {
    param($Object)
    $Object | ConvertTo-Json -Depth 100
}

Write-Host ""
Write-Host "==================== /api/agent-mesh/health ====================" -ForegroundColor Cyan
$Health = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/agent-mesh/health"
Show-Json $Health

Write-Host ""
Write-Host "==================== /api/agent-mesh/service-guard/health ====================" -ForegroundColor Cyan
$Guard = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/agent-mesh/service-guard/health"
Show-Json $Guard

Write-Host ""
Write-Host "==================== /api/agent-mesh/self-healing/health ====================" -ForegroundColor Cyan
$HealingHealth = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/agent-mesh/self-healing/health"
Show-Json $HealingHealth

Write-Host ""
Write-Host "==================== /api/agent-mesh/self-healing/tick ====================" -ForegroundColor Cyan
$HealingTick = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/agent-mesh/self-healing/tick?reason=post_v15_install"
Show-Json $HealingTick

Write-Host ""
Write-Host "==================== /api/agent-mesh/autonomy/tick ====================" -ForegroundColor Cyan
$AutoTick = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/agent-mesh/autonomy/tick?reason=post_v15_install"
Show-Json $AutoTick

Write-Host ""
Write-Host "==================== /api/agent-mesh/service-telemetry ====================" -ForegroundColor Cyan
$Telemetry = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/agent-mesh/service-telemetry"
Show-Json $Telemetry

Write-Host ""
Write-Host "==================== /api/agent-mesh/service-traces ====================" -ForegroundColor Cyan
$Traces = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/agent-mesh/service-traces?limit=25"
Show-Json $Traces

Write-Host ""
Write-Host "==================== /api/agent-mesh/service-resolution/example ====================" -ForegroundColor Cyan
$Resolver = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/agent-mesh/service-resolution/example"
Show-Json $Resolver

Write-Host ""
Write-Host "==================== /api/agent-mesh/autonomy/history ====================" -ForegroundColor Cyan
$History = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/agent-mesh/autonomy/history?limit=10"
Show-Json $History