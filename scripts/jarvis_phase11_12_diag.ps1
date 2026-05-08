param(
    [string]$BaseUrl = "http://127.0.0.1:8015",
    [string]$MissionId = "mission_custom_001"
)

$ErrorActionPreference = "Stop"

Write-Host "== Check primary route existence =="
try {
    $resp = Invoke-WebRequest -Method POST -Uri "$BaseUrl/api/missions/$MissionId/run" -UseBasicParsing
    [pscustomobject]@{
        route = "/api/missions/$MissionId/run"
        status_code = $resp.StatusCode
        ok = $true
    } | ConvertTo-Json -Depth 10
}
catch {
    [pscustomobject]@{
        route = "/api/missions/$MissionId/run"
        ok = $false
        error = $_.Exception.Message
    } | ConvertTo-Json -Depth 10
}

Write-Host "`n== Check mission bridge health =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/mission-bridge/health" | ConvertTo-Json -Depth 10

Write-Host "`n== Check mission bridge record =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/mission-bridge/check/$MissionId" | ConvertTo-Json -Depth 10

Write-Host "`n== Run bridge directly =="
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/autonomy/mission-bridge/run/$MissionId" | ConvertTo-Json -Depth 10

Write-Host "`n== Trigger direct continuation through executor =="
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/autonomy/continue/$MissionId" | ConvertTo-Json -Depth 20

Write-Host "`n== Dashboard after diagnostics =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/dashboard" | ConvertTo-Json -Depth 20
