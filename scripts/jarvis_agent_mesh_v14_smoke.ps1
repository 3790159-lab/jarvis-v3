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
Write-Host "==================== /api/agent-mesh/autonomy/health ====================" -ForegroundColor Cyan
$AutoHealth = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/agent-mesh/autonomy/health"
Show-Json $AutoHealth

Write-Host ""
Write-Host "==================== /api/agent-mesh/autonomy/tick ====================" -ForegroundColor Cyan
$Tick = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/agent-mesh/autonomy/tick?reason=post_install_smoke"
Show-Json $Tick

Write-Host ""
Write-Host "==================== /api/agent-mesh/service-telemetry ====================" -ForegroundColor Cyan
$Telemetry = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/agent-mesh/service-telemetry"
Show-Json $Telemetry

Write-Host ""
Write-Host "==================== /api/agent-mesh/service-traces ====================" -ForegroundColor Cyan
$Traces = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/agent-mesh/service-traces?limit=25"
Show-Json $Traces

Write-Host ""
Write-Host "==================== /api/agent-mesh/autonomy/history ====================" -ForegroundColor Cyan
$History = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/agent-mesh/autonomy/history?limit=10"
Show-Json $History

Write-Host ""
Write-Host "==================== /api/agent-mesh/lifecycle/health ====================" -ForegroundColor Cyan
$Life = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/agent-mesh/lifecycle/health"
Show-Json $Life

Write-Host ""
Write-Host "==================== /api/agent-mesh/knowledge/domains ====================" -ForegroundColor Cyan
$Domains = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/agent-mesh/knowledge/domains?include_items=true"
Show-Json $Domains