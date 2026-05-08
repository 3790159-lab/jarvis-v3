param(
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

$ErrorActionPreference = "Stop"

Write-Host "== Mission graph health =="
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/mission-graph/health" | ConvertTo-Json -Depth 40

Write-Host "`n== Create failure injection graph =="
$failureGraph = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/mission-graph/test/failure-graph"
$failureGraph | ConvertTo-Json -Depth 100

Write-Host "`n== Execute failure graph (expected partial failure) =="
$failedExec = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/mission-graph/missions/$($failureGraph.graph_mission_id)/execute"
$failedExec | ConvertTo-Json -Depth 100

Write-Host "`n== Read failed graph =="
$failedGraph = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/mission-graph/missions/$($failureGraph.graph_mission_id)"
$failedGraph | ConvertTo-Json -Depth 100

Write-Host "`n== Retry failed step =="
$retryResult = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/mission-graph/missions/$($failureGraph.graph_mission_id)/steps/step_content_table/retry"
$retryResult | ConvertTo-Json -Depth 100

Write-Host "`n== Package completed graph =="
$packageResult = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/mission-graph/missions/$($failureGraph.graph_mission_id)/package"
$packageResult | ConvertTo-Json -Depth 100

Write-Host "`n== Read packaged graph =="
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/mission-graph/missions/$($failureGraph.graph_mission_id)" | ConvertTo-Json -Depth 100

Write-Host "`nFailure + retry + package smoke completed successfully."

