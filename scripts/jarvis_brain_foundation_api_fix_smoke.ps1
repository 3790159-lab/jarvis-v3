Set-ExecutionPolicy -Scope Process Bypass -Force
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram"
Set-Location $ProjectRoot

Write-Host "=== API HEALTH ===" -ForegroundColor Cyan
Invoke-RestMethod -Method GET -Uri "http://127.0.0.1:8015/api/brain/health" -TimeoutSec 30 | ConvertTo-Json -Depth 20

Write-Host ""
Write-Host "=== API COMPILE UTF8 BODY ===" -ForegroundColor Cyan

$Payload = @{
    task = "Создай умный pipeline для n8n, Telegram и внешнего API, с проверкой результата и rollback планом"
}

$Json = $Payload | ConvertTo-Json -Depth 10
$Bytes = [System.Text.Encoding]::UTF8.GetBytes($Json)

Invoke-RestMethod `
    -Method POST `
    -Uri "http://127.0.0.1:8015/api/brain/compile" `
    -ContentType "application/json; charset=utf-8" `
    -Body $Bytes `
    -TimeoutSec 60 | ConvertTo-Json -Depth 30

Write-Host ""
Write-Host "BRAIN API UTF8 SMOKE OK" -ForegroundColor Green