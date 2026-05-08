param(
    [string]$BaseUrl = "http://127.0.0.1:8015",
    [string]$MissionId = "mission_custom_001"
)

$ErrorActionPreference = "Stop"

Write-Host "== Health =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/health" | ConvertTo-Json -Depth 10

Write-Host "`n== Schedule continuation =="
Invoke-RestMethod `
  -Method POST `
  -Uri "$BaseUrl/api/autonomy/schedule" `
  -ContentType "application/json" `
  -Body (@{
      mission_id = $MissionId
      delay_seconds = 5
      reason = "phase11_12_smoke"
      payload = @{ source = "smoke_test" }
  } | ConvertTo-Json -Depth 10) | ConvertTo-Json -Depth 10

Write-Host "`n== Publish external signal event =="
Invoke-RestMethod `
  -Method POST `
  -Uri "$BaseUrl/api/autonomy/events" `
  -ContentType "application/json" `
  -Body (@{
      event_type = "external_signal_continue"
      mission_id = $MissionId
      payload = @{ source = "smoke_test"; note = "event driven continuation" }
      severity = "info"
      source = "smoke_script"
      auto_dispatch = $true
  } | ConvertTo-Json -Depth 10) | ConvertTo-Json -Depth 10

Write-Host "`n== Pause mission =="
Invoke-RestMethod `
  -Method POST `
  -Uri "$BaseUrl/api/autonomy/pause/$MissionId" `
  -ContentType "application/json" `
  -Body (@{ reason = "smoke_pause" } | ConvertTo-Json -Depth 10) | ConvertTo-Json -Depth 10

Write-Host "`n== Resume mission =="
Invoke-RestMethod `
  -Method POST `
  -Uri "$BaseUrl/api/autonomy/resume/$MissionId" `
  -ContentType "application/json" `
  -Body (@{ reason = "smoke_resume"; auto_continue = $true } | ConvertTo-Json -Depth 10) | ConvertTo-Json -Depth 10

Write-Host "`n== Evaluate mission =="
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/autonomy/evaluate/$MissionId" | ConvertTo-Json -Depth 10

Write-Host "`n== Dashboard =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/dashboard" | ConvertTo-Json -Depth 20
