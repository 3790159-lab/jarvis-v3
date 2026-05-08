param(
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

$ErrorActionPreference = "Stop"

Write-Host "== Dashboard before =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/dashboard" | ConvertTo-Json -Depth 50

Write-Host "`n== Reconcile runtime =="
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/autonomy/reconcile/runtime" | ConvertTo-Json -Depth 30

Write-Host "`n== Archive jobs older than 1 hour =="
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/autonomy/archive/jobs?older_than_hours=1" | ConvertTo-Json -Depth 30

Write-Host "`n== Archive missions older than 1 hour =="
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/autonomy/archive/missions?older_than_hours=1" | ConvertTo-Json -Depth 30

Write-Host "`n== Dashboard after =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/dashboard" | ConvertTo-Json -Depth 50
