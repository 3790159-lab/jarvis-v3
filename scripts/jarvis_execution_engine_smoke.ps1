param(
    [string]$BaseUrl = "http://127.0.0.1:8015",
    [string]$MissionId = "mission_custom_001"
)

$ErrorActionPreference = "Stop"

Write-Host "== Health =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/health" | ConvertTo-Json -Depth 10

Write-Host "`n== Execute engine step: workflow json_write_verify =="
Invoke-RestMethod `
  -Method POST `
  -Uri "$BaseUrl/api/autonomy/execute-step" `
  -ContentType "application/json" `
  -Body (@{
      step = @{
          mission_id = $MissionId
          type = "workflow"
          action = "json_write_verify"
          requested_by = "engine_smoke"
          origin = "manual"
          reason = "engine_json_write_verify"
          payload = @{
              path = "artifacts/autonomy/engine_test.json"
              data = @{
                  mission = $MissionId
                  source = "engine_smoke"
                  ok = $true
              }
          }
          verify = @{
              type = "status_in"
              values = @("completed")
          }
      }
      context = @{
          mission_id = $MissionId
      }
  } | ConvertTo-Json -Depth 30) | ConvertTo-Json -Depth 50

Write-Host "`n== Execute engine step: graph repairable_file_patch_graph =="
Invoke-RestMethod `
  -Method POST `
  -Uri "$BaseUrl/api/autonomy/execute-step" `
  -ContentType "application/json" `
  -Body (@{
      step = @{
          mission_id = $MissionId
          type = "graph"
          action = "repairable_file_patch_graph"
          requested_by = "engine_smoke"
          origin = "manual"
          reason = "engine_patch_graph"
          payload = @{
              path = "artifacts/autonomy/chain_test.txt"
              search = "omega"
              replace = "sigma"
          }
          verify = @{
              type = "status_in"
              values = @("completed")
          }
      }
      context = @{
          mission_id = $MissionId
      }
  } | ConvertTo-Json -Depth 30) | ConvertTo-Json -Depth 50

Write-Host "`n== Engine executions =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/engine/executions?limit=20" | ConvertTo-Json -Depth 50

Write-Host "`n== Dashboard =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/dashboard" | ConvertTo-Json -Depth 50
