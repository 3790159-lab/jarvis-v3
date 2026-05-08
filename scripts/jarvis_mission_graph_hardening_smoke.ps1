param(
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

$ErrorActionPreference = "Stop"

Write-Host "== Mission graph health =="
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/mission-graph/health" | ConvertTo-Json -Depth 30

Write-Host "`n== Submit planned content graph =="
$body = @{
    objective = "Prepare a YouTube starter pack and content table"
    graph_kind = "content_bundle"
    auto_execute = $false
    constraints = @{
        name = "Retryable Content Pack"
        niche = "AI systems"
        audience = "builders"
        tone = "practical"
        table_name = "Retryable Content Calendar"
    }
} | ConvertTo-Json -Depth 20

$graph = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/mission-graph/submit" -ContentType "application/json" -Body $body
$graph | ConvertTo-Json -Depth 100

Write-Host "`n== Execute planned graph =="
$executed = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/mission-graph/missions/$($graph.graph_mission_id)/execute"
$executed | ConvertTo-Json -Depth 100

Write-Host "`n== Rerun completed graph (should be no-op / safe) =="
$rerunCompleted = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/mission-graph/missions/$($graph.graph_mission_id)/rerun"
$rerunCompleted | ConvertTo-Json -Depth 100

Write-Host "`n== Submit second planned graph for cancellation =="
$cancelBody = @{
    objective = "Build a site and a browser game bundle"
    graph_kind = "site_game_bundle"
    auto_execute = $false
    constraints = @{
        title = "Cancelled Graph Bundle"
        hero = "Cancel before execute"
        subtitle = "Graph cancellation test"
        cta = "Stop"
    }
} | ConvertTo-Json -Depth 20

$cancelGraph = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/mission-graph/submit" -ContentType "application/json" -Body $cancelBody
$cancelGraph | ConvertTo-Json -Depth 100

Write-Host "`n== Cancel second graph =="
$cancelResult = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/mission-graph/missions/$($cancelGraph.graph_mission_id)/cancel"
$cancelResult | ConvertTo-Json -Depth 100

Write-Host "`n== Read cancelled graph =="
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/mission-graph/missions/$($cancelGraph.graph_mission_id)" | ConvertTo-Json -Depth 100

Write-Host "`n== Latest graph runs =="
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/mission-graph/runs" | ConvertTo-Json -Depth 100

Write-Host "`nMission graph hardening smoke completed successfully."
