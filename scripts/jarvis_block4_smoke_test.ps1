param(
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

Write-Host "== Coding agent after fix ==" -ForegroundColor Cyan
$CodingBody = @{
    title = "Write a short Python retry helper"
    description = "Create compact usable backend-oriented code."
    objective = "Verify coding specialized path after stabilization"
    metadata = @{ source = "block4_smoke_test" }
} | ConvertTo-Json -Depth 10
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/agents/coding" -ContentType "application/json" -Body $CodingBody | ConvertTo-Json -Depth 20

Write-Host ""
Write-Host "== Memory write ==" -ForegroundColor Cyan
$WriteBody = @{
    category = "lessons"
    title = "Block 4 memory test"
    content = "Semantic memory layer is active and can store structured notes."
    tags = @("memory","test","jarvis")
    metadata = @{ source = "block4_smoke_test"; stage = "block4" }
} | ConvertTo-Json -Depth 10
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/memory-layer/write" -ContentType "application/json" -Body $WriteBody | ConvertTo-Json -Depth 20

Write-Host ""
Write-Host "== Mission summary write ==" -ForegroundColor Cyan
$SummaryBody = @{
    mission_id = "mission_block4_demo"
    objective = "Verify memory layer integration"
    summary = "Mission memory integration works and writes structured artifacts."
    lessons_learned = @(
        "Specialized agents benefit from fallback chains",
        "Memory should be written in structured categories"
    )
    known_issues = @(
        "Cloud providers are not configured yet"
    )
    metadata = @{ source = "block4_smoke_test"; stage = "block4" }
} | ConvertTo-Json -Depth 20
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/memory-layer/mission-summary" -ContentType "application/json" -Body $SummaryBody | ConvertTo-Json -Depth 20

Write-Host ""
Write-Host "== Memory list summaries ==" -ForegroundColor Cyan
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/memory-layer/list/summaries" | ConvertTo-Json -Depth 20
