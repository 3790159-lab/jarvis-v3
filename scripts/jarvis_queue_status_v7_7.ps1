param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram"
)

$QueuePath = Join-Path $ProjectRoot "state\jarvis_brain\action_queue_v6_4.json"
if (-not (Test-Path $QueuePath)) {
    Write-Host "Queue missing: $QueuePath" -ForegroundColor Yellow
    exit 1
}

$Q = Get-Content $QueuePath -Raw -Encoding UTF8 | ConvertFrom-Json

Write-Host "=== QUEUE STATUS V7.7 ===" -ForegroundColor Cyan
Write-Host "Total:" $Q.items.Count
Write-Host "Pending:" (($Q.items | Where-Object { $_.status -eq "pending" }).Count)
Write-Host "Completed:" (($Q.items | Where-Object { $_.status -eq "completed" }).Count)
Write-Host "Failed:" (($Q.items | Where-Object { $_.status -eq "failed" }).Count)
Write-Host ""

$Q.items |
  Sort-Object status, priority |
  Select-Object id,status,priority,risk,lane,executor |
  Format-Table -AutoSize