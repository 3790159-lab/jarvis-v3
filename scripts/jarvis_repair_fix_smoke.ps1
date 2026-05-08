param(
    [string]$BaseUrl = "http://127.0.0.1:8015",
    [string]$MissionId = "mission_custom_001"
)

$ErrorActionPreference = "Stop"

Write-Host "== Health =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/health" | ConvertTo-Json -Depth 10

Write-Host "`n== Execute repair cleanup_and_unpause =="
Invoke-RestMethod `
  -Method POST `
  -Uri "$BaseUrl/api/autonomy/repairs/execute" `
  -ContentType "application/json" `
  -Body (@{
      repair_id = "cleanup_and_unpause"
      requested_by = "repair_fix_smoke"
      payload = @{
          mission_id = $MissionId
      }
  } | ConvertTo-Json -Depth 20) | ConvertTo-Json -Depth 50

Write-Host "`n== Repair executions =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/repairs/executions?limit=20" | ConvertTo-Json -Depth 50

Write-Host "`n== Dashboard =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/dashboard" | ConvertTo-Json -Depth 50
