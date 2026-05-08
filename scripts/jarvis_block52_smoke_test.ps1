param(
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

$Body = @{
    objective = "Verify mission-run auto memory persistence"
    metadata = @{
        stage = "block52"
        source = "smoke_test"
    }
    mission_result = @{
        mission_id = "mission_block52_demo"
        status = "completed"
        summary = "Mission run completed and should now be auto-persisted into memory."
        task_results = @(
            @{
                task_id = "t1"
                title = "Run AI route health validation"
                status = "completed"
                message = "Health check passed"
                output = @{ status = "healthy" }
            },
            @{
                task_id = "t2"
                title = "Execute coding task"
                status = "completed"
                message = "Coding task returned implementation"
                output = @{ provider = "ollama" }
            },
            @{
                task_id = "t3"
                title = "Persist mission artifacts"
                status = "completed"
                message = "Artifacts and memory records written"
                output = @{ artifact_type = "memory" }
            }
        )
    }
} | ConvertTo-Json -Depth 20

Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/mission-run-memory/write" -ContentType "application/json" -Body $Body | ConvertTo-Json -Depth 20
