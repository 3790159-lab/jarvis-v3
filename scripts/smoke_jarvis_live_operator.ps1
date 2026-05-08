param(
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

Write-Host "== JARVIS LIVE HEALTH ==" -ForegroundColor Cyan
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/jarvis/live-health" | ConvertTo-Json -Depth 20

Write-Host "`n== JARVIS LIVE COMMAND TEST ==" -ForegroundColor Cyan

$BodyObj = @{
    text = "create n8n pipeline with communicator, text or photo brief, 12 content assets, upload to Google Drive without quality loss"
}

$Body = $BodyObj | ConvertTo-Json -Depth 10

Invoke-RestMethod `
    -Method POST `
    -Uri "$BaseUrl/api/jarvis/live-command" `
    -ContentType "application/json; charset=utf-8" `
    -Body $Body | ConvertTo-Json -Depth 30