param(
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

$ErrorActionPreference = "Stop"

Write-Host "== Health =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/health" | ConvertTo-Json -Depth 10

Write-Host "`n== Create baseline file for patch mission =="
$PatchFile = Join-Path (Get-Location) "artifacts\autonomy\real_test_patch_target.txt"
Set-Content -Path $PatchFile -Encoding UTF8 -Value "service_mode=dev`n"

Write-Host "`n== Baseline file created =="
Get-Content $PatchFile

Write-Host "`n== Test prep complete =="
