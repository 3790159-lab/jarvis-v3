param(
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

# Auth for the default-deny guard (audit 2026-07-15). Sends X-API-Key from env.
$ApiKey = $env:JARVIS_INTERNAL_API_KEY
if (-not $ApiKey) { $ApiKey = $env:JARVIS_ADMIN_KEY }
$AuthHeaders = @{}
if ($ApiKey) { $AuthHeaders["X-API-Key"] = $ApiKey }

Write-Host "== JARVIS LIVE HEALTH ==" -ForegroundColor Cyan
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/jarvis/live-health" -Headers $AuthHeaders | ConvertTo-Json -Depth 20

Write-Host "`n== JARVIS LIVE COMMAND TEST ==" -ForegroundColor Cyan

$BodyObj = @{
    text = "create n8n pipeline with communicator, text or photo brief, 12 content assets, upload to Google Drive without quality loss"
}

$Body = $BodyObj | ConvertTo-Json -Depth 10

Invoke-RestMethod `
    -Method POST `
    -Uri "$BaseUrl/api/jarvis/live-command" `
    -Headers $AuthHeaders `
    -ContentType "application/json; charset=utf-8" `
    -Body $Body | ConvertTo-Json -Depth 30