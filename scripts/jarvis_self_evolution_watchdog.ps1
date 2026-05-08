# Jarvis Self Evolution Watchdog Pack
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Base = "http://127.0.0.1:8028"
$Status = Invoke-RestMethod -Method GET -Uri "$Base/api/self-evolution/status" -TimeoutSec 30
if (-not $Status.runner.is_running) {
    Write-Host "runner_not_running" -ForegroundColor Yellow
} else {
    Write-Host "runner_running" -ForegroundColor Green
}
$Status | ConvertTo-Json -Depth 80
