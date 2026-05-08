param(
    [string]$BaseUrl = "http://127.0.0.1:8010"
)

Write-Host ""
Write-Host "== Jarvis Smoke All ==" -ForegroundColor Cyan

Write-Host "`n[1] API health" -ForegroundColor Yellow
Invoke-RestMethod -Method GET -Uri "$BaseUrl/health" | ConvertTo-Json -Depth 20

Write-Host "`n[2] execution health" -ForegroundColor Yellow
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/execution/health" | ConvertTo-Json -Depth 20

Write-Host "`n[3] tools health" -ForegroundColor Yellow
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/tools/health" | ConvertTo-Json -Depth 20

Write-Host "`n[4] feedback health" -ForegroundColor Yellow
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/feedback/health" | ConvertTo-Json -Depth 20

Write-Host "`n[5] mission memory v2 health" -ForegroundColor Yellow
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/mission-memory-v2/health" | ConvertTo-Json -Depth 20

Write-Host "`n[6] hitl health" -ForegroundColor Yellow
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/hitl/health" | ConvertTo-Json -Depth 20

Write-Host "`n[7] runtime routes quick audit" -ForegroundColor Yellow
powershell -ExecutionPolicy Bypass -File ".\jarvis_routes_audit.ps1" -BaseUrl $BaseUrl
if ($LASTEXITCODE -ne 0) {
    throw "Route audit failed"
}
