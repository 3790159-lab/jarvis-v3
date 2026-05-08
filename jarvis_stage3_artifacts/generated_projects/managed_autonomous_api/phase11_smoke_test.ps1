param(
    [string]$BaseUrl = "http://127.0.0.1:8010",
    [string]$MissionId = "mission_phase11_demo"
)

Write-Host ""
Write-Host "== Phase 11 Smoke Test ==" -ForegroundColor Cyan

Write-Host "`n[1] execution health" -ForegroundColor Yellow
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/execution/health" | ConvertTo-Json -Depth 10

Write-Host "`n[2] run mission" -ForegroundColor Yellow
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/execution/missions/$MissionId/run" | ConvertTo-Json -Depth 10

Start-Sleep -Seconds 2

Write-Host "`n[3] journal after 2 sec" -ForegroundColor Yellow
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/execution/missions/$MissionId/journal" | ConvertTo-Json -Depth 20
