param(
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "=== SUPERVISOR AUTOMATION HEALTH ===" -ForegroundColor Cyan
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/supervisor/automation/health" -TimeoutSec 10 | ConvertTo-Json -Depth 20

Write-Host ""
Write-Host "=== SUPERVISOR AUTOMATION PREVIEW ===" -ForegroundColor Cyan
$PreviewBody = @{
    action = "echo"
    intent = "supervisor_preview_smoke"
    source = "jarvis"
    use_test_webhook = $false
    payload = @{
        message = "preview from supervisor runtime"
        test = $true
    }
} | ConvertTo-Json -Depth 20

Invoke-RestMethod `
    -Method POST `
    -Uri "$BaseUrl/api/supervisor/automation/preview" `
    -ContentType "application/json" `
    -Body $PreviewBody | ConvertTo-Json -Depth 20

Write-Host ""
Write-Host "=== SUPERVISOR AUTOMATION RUN ===" -ForegroundColor Cyan
$RunBody = @{
    action = "echo"
    intent = "supervisor_run_smoke"
    mission_id = "supervisor-run-" + [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    task_id = "dispatch-1"
    source = "jarvis"
    use_test_webhook = $false
    payload = @{
        message = "run from supervisor runtime"
        test = $true
    }
} | ConvertTo-Json -Depth 20

Invoke-RestMethod `
    -Method POST `
    -Uri "$BaseUrl/api/supervisor/automation/run" `
    -ContentType "application/json" `
    -Body $RunBody | ConvertTo-Json -Depth 20