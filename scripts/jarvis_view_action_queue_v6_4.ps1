param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram"
)

$QueuePath = Join-Path $ProjectRoot "state\jarvis_brain\action_queue_v6_4.json"

if (-not (Test-Path $QueuePath)) {
    Write-Host "Queue not found: $QueuePath" -ForegroundColor Yellow
    exit 1
}

$Q = Get-Content $QueuePath -Raw -Encoding UTF8 | ConvertFrom-Json

Write-Host "=== JARVIS ACTION QUEUE V6.4 ===" -ForegroundColor Cyan
Write-Host "Queue size:" $Q.items.Count
Write-Host ""

$Q.items |
    Sort-Object priority, created_at |
    Select-Object id, title, status, priority, risk, lane |
    Format-Table -AutoSize