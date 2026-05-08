param(
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

$ErrorActionPreference = "Stop"

Write-Host "== Recovery policy =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/recovery-policy" | ConvertTo-Json -Depth 20

Write-Host "`n== Operator escalations before =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/operator-escalations?limit=20" | ConvertTo-Json -Depth 20

Write-Host "`n== Mission templates =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/mission-templates" | ConvertTo-Json -Depth 20

Write-Host "`n== Create mission: service_health_repair =="
$Mission = Invoke-RestMethod `
  -Method POST `
  -Uri "$BaseUrl/api/autonomy/missions" `
  -ContentType "application/json" `
  -Body (@{
      template_id = "service_health_repair"
      payload = @{
          port = 8015
          allow_kill = $false
      }
      metadata = @{
          created_by = "jarvis_next_block_test"
          scope = "controlled"
      }
  } | ConvertTo-Json -Depth 30)

$Mission | ConvertTo-Json -Depth 40
$MissionId = $Mission.mission.mission_id

Write-Host "`n== Run mission =="
Invoke-RestMethod `
  -Method POST `
  -Uri "$BaseUrl/api/autonomy/missions/$MissionId/run" `
  -ContentType "application/json" `
  -Body (@{ max_steps = 20 } | ConvertTo-Json -Depth 10) | ConvertTo-Json -Depth 50

Write-Host "`n== Mission details =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/missions/$MissionId" | ConvertTo-Json -Depth 50

Write-Host "`n== Operator escalations after =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/operator-escalations?limit=20" | ConvertTo-Json -Depth 20

Write-Host "`n== Dashboard =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/dashboard" | ConvertTo-Json -Depth 50
