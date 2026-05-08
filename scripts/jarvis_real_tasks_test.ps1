param(
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

$ErrorActionPreference = "Stop"

Write-Host "== Mission templates =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/mission-templates" | ConvertTo-Json -Depth 20

Write-Host "`n== Create mission: file_patch_and_verify =="
$MissionA = Invoke-RestMethod `
  -Method POST `
  -Uri "$BaseUrl/api/autonomy/missions" `
  -ContentType "application/json" `
  -Body (@{
      template_id = "file_patch_and_verify"
      payload = @{
          mission_id = "mission_custom_001"
          path = "artifacts/autonomy/real_test_patch_target.txt"
          search = "service_mode=dev"
          replace = "service_mode=prod"
      }
      metadata = @{
          created_by = "jarvis_real_tasks_test"
          scope = "safe_real_test"
      }
  } | ConvertTo-Json -Depth 30)

$MissionA | ConvertTo-Json -Depth 40
$MissionAId = $MissionA.mission.mission_id

Write-Host "`n== Run mission A =="
Invoke-RestMethod `
  -Method POST `
  -Uri "$BaseUrl/api/autonomy/missions/$MissionAId/run" `
  -ContentType "application/json" `
  -Body (@{ max_steps = 20 } | ConvertTo-Json -Depth 10) | ConvertTo-Json -Depth 50

Write-Host "`n== Mission A details =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/missions/$MissionAId" | ConvertTo-Json -Depth 50

Write-Host "`n== Mission A history =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/missions/$MissionAId/history?limit=50" | ConvertTo-Json -Depth 50

Write-Host "`n== Create mission: api_health_restart_verify =="
$MissionB = Invoke-RestMethod `
  -Method POST `
  -Uri "$BaseUrl/api/autonomy/missions" `
  -ContentType "application/json" `
  -Body (@{
      template_id = "api_health_restart_verify"
      payload = @{
          port = 8015
          allow_kill = $false
          health_url = "http://127.0.0.1:8015/api/autonomy/health"
      }
      metadata = @{
          created_by = "jarvis_real_tasks_test"
          scope = "safe_real_test"
      }
  } | ConvertTo-Json -Depth 30)

$MissionB | ConvertTo-Json -Depth 40
$MissionBId = $MissionB.mission.mission_id

Write-Host "`n== Run mission B =="
Invoke-RestMethod `
  -Method POST `
  -Uri "$BaseUrl/api/autonomy/missions/$MissionBId/run" `
  -ContentType "application/json" `
  -Body (@{ max_steps = 20 } | ConvertTo-Json -Depth 10) | ConvertTo-Json -Depth 50

Write-Host "`n== Mission B details =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/missions/$MissionBId" | ConvertTo-Json -Depth 50

Write-Host "`n== Dashboard =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/dashboard" | ConvertTo-Json -Depth 50
