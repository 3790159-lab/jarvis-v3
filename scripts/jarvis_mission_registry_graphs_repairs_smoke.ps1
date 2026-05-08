param(
    [string]$BaseUrl = "http://127.0.0.1:8015",
    [string]$MissionId = "mission_custom_001"
)

$ErrorActionPreference = "Stop"

Write-Host "== Health =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/health" | ConvertTo-Json -Depth 10

Write-Host "`n== Mission registry =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/mission-registry" | ConvertTo-Json -Depth 20

Write-Host "`n== Graph templates =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/graphs" | ConvertTo-Json -Depth 20

Write-Host "`n== Repair templates =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/repairs" | ConvertTo-Json -Depth 20

Write-Host "`n== Execute repair cleanup_and_unpause =="
Invoke-RestMethod `
  -Method POST `
  -Uri "$BaseUrl/api/autonomy/repairs/execute" `
  -ContentType "application/json" `
  -Body (@{
      repair_id = "cleanup_and_unpause"
      requested_by = "registry_graph_repair_smoke"
      payload = @{
          mission_id = $MissionId
      }
  } | ConvertTo-Json -Depth 20) | ConvertTo-Json -Depth 40

Write-Host "`n== Execute graph repairable_file_patch_graph =="
Invoke-RestMethod `
  -Method POST `
  -Uri "$BaseUrl/api/autonomy/graphs/execute" `
  -ContentType "application/json" `
  -Body (@{
      graph_id = "repairable_file_patch_graph"
      requested_by = "registry_graph_repair_smoke"
      payload = @{
          path = "artifacts/autonomy/chain_test.txt"
          search = "delta"
          replace = "omega"
      }
  } | ConvertTo-Json -Depth 20) | ConvertTo-Json -Depth 40

Write-Host "`n== Workflow executions =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/workflows/executions?limit=20" | ConvertTo-Json -Depth 40

Write-Host "`n== Graph executions =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/graphs/executions?limit=20" | ConvertTo-Json -Depth 40

Write-Host "`n== Repair executions =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/repairs/executions?limit=20" | ConvertTo-Json -Depth 40

Write-Host "`n== Dashboard =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/dashboard" | ConvertTo-Json -Depth 50
