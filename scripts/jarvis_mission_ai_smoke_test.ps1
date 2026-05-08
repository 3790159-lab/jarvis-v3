param(
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

Write-Host "== Mission AI: classify task ==" -ForegroundColor Cyan
$ClassifyBody = @{
    objective = "Improve Jarvis mission execution quality and debugging"
    title = "Create a FastAPI endpoint for AI mission execution"
    description = "Write and organize backend logic for routing mission tasks to the proper AI agent."
} | ConvertTo-Json -Depth 10
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/mission-ai/classify-task" -ContentType "application/json" -Body $ClassifyBody | ConvertTo-Json -Depth 10

Write-Host ""
Write-Host "== Mission AI: execute multi-task mission (stabilized) ==" -ForegroundColor Cyan
$MissionBody = @{
    mission_id = "mission_block2_demo_fix"
    objective = "Strengthen Jarvis with task-aware AI mission execution"
    stop_on_error = $false
    tasks = @(
        @{
            task_id = "t1"
            title = "Design the architecture choice for routing agent tasks"
            description = "Compare tradeoffs and propose a stable approach for the next implementation step."
            metadata = @{ priority = "high" }
        },
        @{
            task_id = "t2"
            title = "Write a Python retry helper for async task execution"
            description = "Create maintainable backend code."
            metadata = @{ priority = "high" }
        },
        @{
            task_id = "t3"
            title = "Research best practices for AI supervisor mission orchestration"
            description = "Provide a concise practical summary with a few actionable recommendations."
            metadata = @{ priority = "medium" }
        }
    )
} | ConvertTo-Json -Depth 20

Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/mission-ai/execute" -ContentType "application/json" -Body $MissionBody | ConvertTo-Json -Depth 20
