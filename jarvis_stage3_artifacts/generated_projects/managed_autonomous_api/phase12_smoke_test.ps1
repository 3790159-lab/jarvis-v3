param(
    [string]$BaseUrl = "http://127.0.0.1:8010"
)

Write-Host ""
Write-Host "== Phase 12 Hardened Smoke Test ==" -ForegroundColor Cyan

Write-Host "`n[1] tools health" -ForegroundColor Yellow
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/tools/health" | ConvertTo-Json -Depth 20

Write-Host "`n[2] tools config" -ForegroundColor Yellow
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/tools/config" | ConvertTo-Json -Depth 20

Write-Host "`n[3] http allowlisted localhost health" -ForegroundColor Yellow
$httpBody = @{
    url = "$BaseUrl/health"
} | ConvertTo-Json
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/tools/http/get" -ContentType "application/json" -Body $httpBody | ConvertTo-Json -Depth 20

Write-Host "`n[4] shell blocked example" -ForegroundColor Yellow
$shellBlocked = @{
    command = "powershell Get-ChildItem"
} | ConvertTo-Json
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/tools/shell" -ContentType "application/json" -Body $shellBlocked | ConvertTo-Json -Depth 20

Write-Host "`n[5] shell allowed python example" -ForegroundColor Yellow
$shellAllowed = @{
    command = "python --version"
} | ConvertTo-Json
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/tools/shell" -ContentType "application/json" -Body $shellAllowed | ConvertTo-Json -Depth 20

Write-Host "`n[6] filesystem write allowed example" -ForegroundColor Yellow
$fileWrite = @{
    path = ".\artifacts\phase12_demo.txt"
    content = "phase12 gateway hardened test"
} | ConvertTo-Json
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/tools/filesystem/write" -ContentType "application/json" -Body $fileWrite | ConvertTo-Json -Depth 20

Write-Host "`n[7] filesystem read allowed example" -ForegroundColor Yellow
$fileRead = @{
    path = ".\artifacts\phase12_demo.txt"
} | ConvertTo-Json
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/tools/filesystem/read" -ContentType "application/json" -Body $fileRead | ConvertTo-Json -Depth 20

Write-Host "`n[8] filesystem blocked example" -ForegroundColor Yellow
$fileBlocked = @{
    path = "C:\Windows\System32\drivers\etc\hosts"
} | ConvertTo-Json
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/tools/filesystem/read" -ContentType "application/json" -Body $fileBlocked | ConvertTo-Json -Depth 20
