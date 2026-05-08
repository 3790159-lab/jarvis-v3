param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram"
)

Set-ExecutionPolicy -Scope Process Bypass -Force
Set-Location $ProjectRoot

Write-Host "=== JARVIS AUTOLOOP 24/7 STATUS ===" -ForegroundColor Cyan

$LockPath = "state\jarvis_brain\autoloop_24_7.lock"
$StopPath = "state\jarvis_brain\autoloop_24_7.stop"

if (Test-Path $LockPath) {
    Write-Host "RUNNING lock:" -ForegroundColor Green
    Get-Content $LockPath
} else {
    Write-Host "No active lock." -ForegroundColor Yellow
}

if (Test-Path $StopPath) {
    Write-Host "STOP requested:" -ForegroundColor Yellow
    Get-Content $StopPath
}

Write-Host ""
Write-Host "Recent AutoLoop runs:" -ForegroundColor Cyan
Get-ChildItem "jarvis_stage3_artifacts\autoloop_24_7\runs" -Directory -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 5 FullName, LastWriteTime |
    Format-Table -AutoSize

Write-Host ""
Write-Host "Backend health:" -ForegroundColor Cyan
try {
    Invoke-RestMethod -Method GET -Uri "http://127.0.0.1:8015/health" -TimeoutSec 8 | ConvertTo-Json -Depth 10
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
}

Write-Host ""
Write-Host "Time Brain now:" -ForegroundColor Cyan
try {
    Invoke-RestMethod -Method GET -Uri "http://127.0.0.1:8015/api/time-brain/now" -TimeoutSec 8 | ConvertTo-Json -Depth 10
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
}