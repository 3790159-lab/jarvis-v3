param(
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "=== SUPERVISOR PIPELINE HEALTH ===" -ForegroundColor Cyan
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/supervisor/pipeline/health" -TimeoutSec 10 | ConvertTo-Json -Depth 20

Write-Host ""
Write-Host "=== SUPERVISOR PIPELINE PREVIEW ===" -ForegroundColor Cyan
$PreviewBody = @{
    pipeline_name = "jarvis_two_step_preview"
    steps = @(
        @{
            step_id = "step-1"
            action = "echo.step1"
            intent = "pipeline_preview_step1"
            payload = @{
                message = "preview step 1"
                order = 1
            }
        },
        @{
            step_id = "step-2"
            action = "echo.step2"
            intent = "pipeline_preview_step2"
            payload = @{
                message = "preview step 2"
                order = 2
            }
        }
    )
} | ConvertTo-Json -Depth 20

Invoke-RestMethod `
    -Method POST `
    -Uri "$BaseUrl/api/supervisor/pipeline/preview" `
    -ContentType "application/json" `
    -Body $PreviewBody | ConvertTo-Json -Depth 20

Write-Host ""
Write-Host "=== SUPERVISOR PIPELINE RUN ===" -ForegroundColor Cyan
$RunBody = @{
    pipeline_name = "jarvis_two_step_run"
    mission_id = "pipeline-" + [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    steps = @(
        @{
            step_id = "step-1"
            action = "echo.step1"
            intent = "pipeline_run_step1"
            payload = @{
                message = "run step 1"
                order = 1
            }
        },
        @{
            step_id = "step-2"
            action = "echo.step2"
            intent = "pipeline_run_step2"
            payload = @{
                message = "run step 2"
                order = 2
            }
        }
    )
} | ConvertTo-Json -Depth 20

Invoke-RestMethod `
    -Method POST `
    -Uri "$BaseUrl/api/supervisor/pipeline/run" `
    -ContentType "application/json" `
    -Body $RunBody | ConvertTo-Json -Depth 20