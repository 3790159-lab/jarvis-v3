param(
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

$Body = @{
    mission_id = "mission_block51_demo"
    objective = "Verify automatic mission memory pipeline"
    status = "completed"
    summary = "Mission completed successfully with stable routing, execution, and memory write flow."
    task_results = @(
        @{
            task_id = "t1"
            title = "Check AI router health"
            status = "completed"
            message = "Health endpoint returned healthy status"
            output = @{ status = "healthy" }
        },
        @{
            task_id = "t2"
            title = "Write mission summary to memory"
            status = "completed"
            message = "Mission summary written successfully"
            output = @{ category = "summaries" }
        },
        @{
            task_id = "t3"
            title = "Validate vault export"
            status = "completed"
            message = "Obsidian export finished"
            output = @{ vault = "obsidian_vault" }
        }
    )
    metadata = @{
        stage = "block51"
        source = "smoke_test"
    }
} | ConvertTo-Json -Depth 20

Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/mission-memory/run" -ContentType "application/json" -Body $Body | ConvertTo-Json -Depth 20
