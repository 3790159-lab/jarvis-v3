param(
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

$ErrorActionPreference = "Stop"

Write-Host "== Health =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/health" | ConvertTo-Json -Depth 10

Write-Host "`n== Mission templates =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/mission-templates" | ConvertTo-Json -Depth 20

Write-Host "`n== Create mission from template json_config_roundtrip =="
$created = Invoke-RestMethod `
  -Method POST `
  -Uri "$BaseUrl/api/autonomy/missions" `
  -ContentType "application/json" `
  -Body (@{
      template_id = "json_config_roundtrip"
      payload = @{
          path = "artifacts/autonomy/mission_loop_test.json"
          data = @{
              source = "mission_loop_smoke"
              ok = $true
              version = 1
          }
      }
      metadata = @{
          created_by = "jarvis_autonomous_mission_loop_smoke"
      }
  } | ConvertTo-Json -Depth 30)

$created | ConvertTo-Json -Depth 40
$MissionId = $created.mission.mission_id

Write-Host "`n== Run mission =="
Invoke-RestMethod `
  -Method POST `
  -Uri "$BaseUrl/api/autonomy/missions/$MissionId/run" `
  -ContentType "application/json" `
  -Body (@{
      max_steps = 20
  } | ConvertTo-Json -Depth 10) | ConvertTo-Json -Depth 50

Write-Host "`n== Mission details =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/missions/$MissionId" | ConvertTo-Json -Depth 50

Write-Host "`n== Mission history =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/missions/$MissionId/history?limit=50" | ConvertTo-Json -Depth 50

Write-Host "`n== Dashboard =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/dashboard" | ConvertTo-Json -Depth 50
