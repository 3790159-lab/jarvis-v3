param(
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

Write-Host "== Specialized Agent: coding ==" -ForegroundColor Cyan
$CodingBody = @{
    title = "Write a Python helper for safe retry with exponential backoff"
    description = "Create concise maintainable backend-oriented code."
    objective = "Strengthen Jarvis coding execution quality"
    metadata = @{ source = "block3_smoke_test" }
} | ConvertTo-Json -Depth 10
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/agents/coding" -ContentType "application/json" -Body $CodingBody | ConvertTo-Json -Depth 20

Write-Host ""
Write-Host "== Specialized Agent: research ==" -ForegroundColor Cyan
$ResearchBody = @{
    title = "Research best practices for AI mission orchestration"
    description = "Provide a concise practical summary with actionable points."
    objective = "Strengthen Jarvis research execution quality"
    metadata = @{ source = "block3_smoke_test" }
} | ConvertTo-Json -Depth 10
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/agents/research" -ContentType "application/json" -Body $ResearchBody | ConvertTo-Json -Depth 20

Write-Host ""
Write-Host "== Specialized Agent: reasoning ==" -ForegroundColor Cyan
$ReasoningBody = @{
    title = "Choose the next architecture priority for Jarvis"
    description = "Compare tradeoffs between memory layer and external cloud coding executor."
    objective = "Support next roadmap decision"
    metadata = @{ source = "block3_smoke_test" }
} | ConvertTo-Json -Depth 10
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/agents/reasoning" -ContentType "application/json" -Body $ReasoningBody | ConvertTo-Json -Depth 20

Write-Host ""
Write-Host "== Mission AI integration after specialized layer ==" -ForegroundColor Cyan
$MissionBody = @{
    mission_id = "mission_block3_demo"
    objective = "Strengthen Jarvis with specialized agent execution"
    stop_on_error = $false
    tasks = @(
        @{
            task_id = "t1"
            title = "Design a robust architecture decision for AI task routing"
            description = "Provide structured reasoning and next step."
            metadata = @{ priority = "high" }
        },
        @{
            task_id = "t2"
            title = "Write a Python helper for async retry execution"
            description = "Create implementation-focused code."
            metadata = @{ priority = "high" }
        },
        @{
            task_id = "t3"
            title = "Research best practices for agent orchestration"
            description = "Provide concise practical findings."
            metadata = @{ priority = "medium" }
        }
    )
} | ConvertTo-Json -Depth 20

Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/mission-ai/execute" -ContentType "application/json" -Body $MissionBody | ConvertTo-Json -Depth 20
