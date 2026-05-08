param(
    [string]$StackDir = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram\n8n_docker_strong"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
Set-Location -Path $StackDir

Write-Host "=== docker compose ps ===" -ForegroundColor Green
docker compose ps

Write-Host ""
Write-Host "=== main logs ===" -ForegroundColor Green
docker compose logs n8n-main --tail 80

Write-Host ""
Write-Host "=== worker logs ===" -ForegroundColor Green
docker compose logs n8n-worker --tail 80

Write-Host ""
Write-Host "=== runners logs ===" -ForegroundColor Green
docker compose logs n8n-runners --tail 80

Write-Host ""
Write-Host "=== bridge logs ===" -ForegroundColor Green
docker compose logs jarvis-bridge --tail 80