param(
    [string]$BaseUrl = "http://127.0.0.1:8015",
    [int]$AutonomyIntervalSeconds = 600,
    [bool]$EnableLiveAfterVerify = $false
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
Write-Host "==================== /api/agent-mesh/autonomy/enable ====================" -ForegroundColor Cyan
$AutoEnable = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/agent-mesh/autonomy/enable?interval_seconds=$AutonomyIntervalSeconds"
Show-Json $AutoEnable

Write-Host ""
Write-Host "==================== /api/agent-mesh/traces/rebuild ====================" -ForegroundColor Cyan
$Rebuild = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/agent-mesh/traces/rebuild"
Show-Json $Rebuild

Write-Host ""
Write-Host "==================== /api/agent-mesh/n8n/health ====================" -ForegroundColor Cyan
$N8NHealth = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/agent-mesh/n8n/health"
Show-Json $N8NHealth

Write-Host ""
Write-Host "==================== /api/agent-mesh/n8n/verify ====================" -ForegroundColor Cyan
$N8NVerify = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/agent-mesh/n8n/verify"
Show-Json $N8NVerify

Write-Host ""
Write-Host "==================== /api/agent-mesh/n8n/test-webhook?dry_run=true ====================" -ForegroundColor Cyan
$N8NTest = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/agent-mesh/n8n/test-webhook?dry_run=true"
Show-Json $N8NTest

if ($EnableLiveAfterVerify -and $N8NVerify.result.status -eq "ok" -and $N8NTest.result.status -eq "ok") {
    Write-Host ""
    Write-Host "==================== /api/agent-mesh/n8n/promote-live ====================" -ForegroundColor Cyan
    $Promote = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/agent-mesh/n8n/promote-live?enabled=true"
    Show-Json $Promote
}

Write-Host ""
Write-Host "==================== /api/agent-mesh/real-exec/health ====================" -ForegroundColor Cyan
$RealExec = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/agent-mesh/real-exec/health"
Show-Json $RealExec

Write-Host ""
Write-Host "==================== /api/agent-mesh/service-telemetry ====================" -ForegroundColor Cyan
$Telemetry = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/agent-mesh/service-telemetry"
Show-Json $Telemetry

Write-Host ""
Write-Host "==================== /api/agent-mesh/service-traces ====================" -ForegroundColor Cyan
$Traces = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/agent-mesh/service-traces?limit=25"
Show-Json $Traces