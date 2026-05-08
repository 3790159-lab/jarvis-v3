param(
    [string]$BaseUrl = "http://127.0.0.1:8015",
    [string]$MissionId = "mission_custom_001"
)

$ErrorActionPreference = "Stop"

Write-Host "== Health =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/health" | ConvertTo-Json -Depth 10

Write-Host "`n== Normalize jobs and approvals =="
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/autonomy/maintenance/normalize" | ConvertTo-Json -Depth 20

Write-Host "`n== Cleanup runtime =="
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/autonomy/maintenance/cleanup-runtime" | ConvertTo-Json -Depth 20

Write-Host "`n== Tools list =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/tools" | ConvertTo-Json -Depth 30

Write-Host "`n== Execute tool plan (write + append + read) =="
Invoke-RestMethod `
  -Method POST `
  -Uri "$BaseUrl/api/autonomy/tools/execute-plan" `
  -ContentType "application/json" `
  -Body (@{
      requested_by = "next_tools_smoke"
      steps = @(
          @{
              tool_id = "file_write"
              payload = @{
                  path = "artifacts/autonomy/plan_test.txt"
                  content = "line 1`n"
              }
          },
          @{
              tool_id = "file_append"
              payload = @{
                  path = "artifacts/autonomy/plan_test.txt"
                  content = "line 2`n"
              }
          },
          @{
              tool_id = "file_read"
              payload = @{
                  path = "artifacts/autonomy/plan_test.txt"
              }
          }
      )
  } | ConvertTo-Json -Depth 20) | ConvertTo-Json -Depth 40

Write-Host "`n== JSON write =="
Invoke-RestMethod `
  -Method POST `
  -Uri "$BaseUrl/api/autonomy/tools/execute" `
  -ContentType "application/json" `
  -Body (@{
      tool_id = "json_write"
      requested_by = "next_tools_smoke"
      payload = @{
          path = "artifacts/autonomy/sample.json"
          data = @{
              mission = $MissionId
              stage = "tools_block"
              ok = $true
          }
      }
  } | ConvertTo-Json -Depth 20) | ConvertTo-Json -Depth 30

Write-Host "`n== JSON read =="
Invoke-RestMethod `
  -Method POST `
  -Uri "$BaseUrl/api/autonomy/tools/execute" `
  -ContentType "application/json" `
  -Body (@{
      tool_id = "json_read"
      requested_by = "next_tools_smoke"
      payload = @{
          path = "artifacts/autonomy/sample.json"
      }
  } | ConvertTo-Json -Depth 20) | ConvertTo-Json -Depth 30

Write-Host "`n== Port inspection =="
Invoke-RestMethod `
  -Method POST `
  -Uri "$BaseUrl/api/autonomy/tools/execute" `
  -ContentType "application/json" `
  -Body (@{
      tool_id = "process_inspect_port"
      requested_by = "next_tools_smoke"
      payload = @{
          port = 8015
      }
  } | ConvertTo-Json -Depth 20) | ConvertTo-Json -Depth 30

Write-Host "`n== Dashboard =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/dashboard" | ConvertTo-Json -Depth 50
