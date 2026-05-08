param(
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

$ErrorActionPreference = "Stop"

Write-Host "== Runtime bridge health =="
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/runtime-bridge/health" | ConvertTo-Json -Depth 20

Write-Host "`n== Submit planned-only mission =="
$plannedBody = @{
    objective = "Prepare a YouTube starter pack for an AI automation channel"
    constraints = @{
        name = "AI Automation Runtime Pack"
        niche = "AI automation"
        audience = "builders and beginners"
        tone = "clear and practical"
    }
    auto_execute = $false
} | ConvertTo-Json -Depth 20

$plannedResult = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/runtime-bridge/submit" -ContentType "application/json" -Body $plannedBody
$plannedResult | ConvertTo-Json -Depth 80

Write-Host "`n== Read planned mission =="
$plannedMission = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/runtime-bridge/missions/$($plannedResult.mission_id)"
$plannedMission | ConvertTo-Json -Depth 80

Write-Host "`n== Execute planned mission =="
$executedPlanned = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/runtime-bridge/missions/$($plannedResult.mission_id)/execute"
$executedPlanned | ConvertTo-Json -Depth 80

Write-Host "`n== Read executed mission =="
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/runtime-bridge/missions/$($plannedResult.mission_id)" | ConvertTo-Json -Depth 80

Write-Host "`n== Submit second planned-only mission for cancellation =="
$cancelBody = @{
    objective = "Create a landing page for a cancelled demo"
    constraints = @{
        title = "Cancelled Demo"
        hero = "This should not execute"
        subtitle = "Cancellation path test"
        cta = "Cancelled"
    }
    auto_execute = $false
} | ConvertTo-Json -Depth 20

$cancelResult = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/runtime-bridge/submit" -ContentType "application/json" -Body $cancelBody
$cancelResult | ConvertTo-Json -Depth 80

Write-Host "`n== Cancel mission =="
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/runtime-bridge/missions/$($cancelResult.mission_id)/cancel" | ConvertTo-Json -Depth 80

Write-Host "`n== Read cancelled mission =="
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/runtime-bridge/missions/$($cancelResult.mission_id)" | ConvertTo-Json -Depth 80

Write-Host "`n== Latest runtime bridge runs =="
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/runtime-bridge/runs" | ConvertTo-Json -Depth 80

Write-Host "`nRuntime bridge hardening smoke completed successfully."
