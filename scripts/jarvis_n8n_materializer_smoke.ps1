param(
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "=== MATERIALIZER HEALTH ===" -ForegroundColor Cyan
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/supervisor/materializer/health" -TimeoutSec 20 | ConvertTo-Json -Depth 20

Write-Host ""
Write-Host "=== MATERIALIZER RUN ===" -ForegroundColor Cyan
$Body = @{
    workflow_name = "jarvis_autonomous_materialized_demo"
    webhook_path = "jarvis/autonomous_materialized_demo"
    description = "Autonomously generated and imported by Jarvis into n8n"
    activate = $true
    steps = @(
        @{
            step_id = "step-1"
            action = "echo.step1"
            intent = "materialized_demo_step1"
            payload = @{
                message = "materialized step 1"
                order = 1
            }
        },
        @{
            step_id = "step-2"
            action = "echo.step2"
            intent = "materialized_demo_step2"
            payload = @{
                message = "materialized step 2"
                order = 2
            }
        },
        @{
            step_id = "step-3"
            action = "echo.step3"
            intent = "materialized_demo_step3"
            payload = @{
                message = "materialized step 3"
                order = 3
            }
        }
    )
} | ConvertTo-Json -Depth 20

Invoke-RestMethod `
    -Method POST `
    -Uri "$BaseUrl/api/supervisor/materializer/run" `
    -ContentType "application/json" `
    -Body $Body | ConvertTo-Json -Depth 20