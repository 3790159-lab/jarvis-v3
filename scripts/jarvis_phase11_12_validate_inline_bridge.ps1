param(
    [string]$BaseUrl = "http://127.0.0.1:8015",
    [string]$MissionId = "mission_custom_001"
)

$ErrorActionPreference = "Stop"

Write-Host "== Inline bridge health =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/mission-bridge/health" | ConvertTo-Json -Depth 10

Write-Host "`n== Inline bridge check =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/mission-bridge/check/$MissionId" | ConvertTo-Json -Depth 10

Write-Host "`n== Reset failed runtime/jobs =="
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/autonomy/admin/reset/$MissionId" | ConvertTo-Json -Depth 10

Write-Host "`n== Direct bridge run =="
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/autonomy/mission-bridge/run/$MissionId" | ConvertTo-Json -Depth 10

Write-Host "`n== Executor continue now =="
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/autonomy/continue/$MissionId" | ConvertTo-Json -Depth 20

Write-Host "`n== Dashboard after continue =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/dashboard" | ConvertTo-Json -Depth 20
