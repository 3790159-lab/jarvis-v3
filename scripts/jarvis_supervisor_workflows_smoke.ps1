param(
    [string]$BaseUrl = "http://127.0.0.1:8015",
    [string]$MissionId = "mission_custom_001"
)

$ErrorActionPreference = "Stop"

Write-Host "== Health =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/health" | ConvertTo-Json -Depth 10

Write-Host "`n== Workflow templates =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/workflows" | ConvertTo-Json -Depth 20

Write-Host "`n== Execute json_write_verify workflow =="
Invoke-RestMethod `
  -Method POST `
  -Uri "$BaseUrl/api/autonomy/workflows/execute" `
  -ContentType "application/json" `
  -Body (@{
      workflow_id = "json_write_verify"
      requested_by = "workflow_smoke"
      payload = @{
          path = "artifacts/autonomy/workflow_sample.json"
          data = @{
              mission = $MissionId
              workflow = "json_write_verify"
              ok = $true
          }
      }
  } | ConvertTo-Json -Depth 20) | ConvertTo-Json -Depth 40

Write-Host "`n== Execute file_patch_verify workflow =="
Invoke-RestMethod `
  -Method POST `
  -Uri "$BaseUrl/api/autonomy/workflows/execute" `
  -ContentType "application/json" `
  -Body (@{
      workflow_id = "file_patch_verify"
      requested_by = "workflow_smoke"
      payload = @{
          path = "artifacts/autonomy/chain_test.txt"
          search = "gamma"
          replace = "delta"
      }
  } | ConvertTo-Json -Depth 20) | ConvertTo-Json -Depth 40

Write-Host "`n== Workflow executions =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/workflows/executions?limit=20" | ConvertTo-Json -Depth 40

Write-Host "`n== Dashboard =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/dashboard" | ConvertTo-Json -Depth 50
