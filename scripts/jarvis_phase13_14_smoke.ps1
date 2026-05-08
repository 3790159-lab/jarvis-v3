param(
    [string]$BaseUrl = "http://127.0.0.1:8015",
    [string]$MissionId = "mission_custom_001"
)

$ErrorActionPreference = "Stop"

Write-Host "== Health =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/health" | ConvertTo-Json -Depth 10

Write-Host "`n== Current Policy =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/policy" | ConvertTo-Json -Depth 10

Write-Host "`n== Update Policy (tight guard for test) =="
Invoke-RestMethod `
  -Method POST `
  -Uri "$BaseUrl/api/autonomy/policy" `
  -ContentType "application/json" `
  -Body (@{
      max_runs_per_hour = 2
      max_replans_per_hour = 2
      max_consecutive_failures = 2
      max_pending_jobs_per_mission = 2
      auto_pause_on_instability = $true
      instability_window_minutes = 60
  } | ConvertTo-Json -Depth 10) | ConvertTo-Json -Depth 10

Write-Host "`n== Guard Evaluate =="
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/autonomy/guard/evaluate/$MissionId" | ConvertTo-Json -Depth 20

Write-Host "`n== Create Memory Snapshot =="
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/autonomy/memory/snapshot/$MissionId?trigger=phase13_14_smoke" | ConvertTo-Json -Depth 20

Write-Host "`n== Resume Bundle =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/memory/resume-bundle/$MissionId" | ConvertTo-Json -Depth 20

Write-Host "`n== Dashboard =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/dashboard" | ConvertTo-Json -Depth 30
