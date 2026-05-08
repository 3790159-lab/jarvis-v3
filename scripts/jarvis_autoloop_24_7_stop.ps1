param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram"
)

Set-ExecutionPolicy -Scope Process Bypass -Force
Set-Location $ProjectRoot

$StopPath = "state\jarvis_brain\autoloop_24_7.stop"
Set-Content -Path $StopPath -Value ("stop_requested_at=" + (Get-Date).ToString("o")) -Encoding UTF8

Write-Host "Stop requested. AutoLoop will exit after current tick." -ForegroundColor Yellow
Write-Host $StopPath -ForegroundColor Yellow