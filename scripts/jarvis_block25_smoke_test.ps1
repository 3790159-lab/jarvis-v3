param(
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

$ErrorActionPreference = "Stop"

Write-Host "== Tool chains health ==" -ForegroundColor Cyan
$health = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/tools/chains/health"
$health | ConvertTo-Json -Depth 20

Write-Host "== Tool chain plan preview ==" -ForegroundColor Cyan
$planBody = @{
    objective = "Create a file, read it, process it, and save processed output"
    step = @{
        title = "Chained auto step"
        description = "create a file, read it, process it, and save processed output"
        task_type = "coding"
        metadata = @{
            phase = "execute"
            router_mode = "hybrid"
            chain_input_name = "block25_input.txt"
            chain_output_name = "block25_output.txt"
        }
    }
} | ConvertTo-Json -Depth 30
$plan = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/tools/chains/plan" -ContentType "application/json" -Body $planBody
$plan | ConvertTo-Json -Depth 40

Write-Host "== Chained multistep ==" -ForegroundColor Cyan
$missionId = "mission_block25_" + ([guid]::NewGuid().ToString("N").Substring(0,8))
$body = @{
    mission_id = $missionId
    objective = "Create a file, read it, process it, and save processed output"
    steps = @(
        @{
            step_id = "chain_step"
            title = "Chained auto step"
            description = "create a file, read it, process it, and save processed output"
            task_type = "coding"
            preferred_provider = "ollama"
            metadata = @{
                phase = "execute"
                router_mode = "hybrid"
                chain_input_name = "block25_input.txt"
                chain_output_name = "block25_output.txt"
            }
        }
    )
} | ConvertTo-Json -Depth 40
$result = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/missions/multistep/execute" -ContentType "application/json" -Body $body
$result | ConvertTo-Json -Depth 60

Write-Host "Block 2.5 smoke test finished." -ForegroundColor Green