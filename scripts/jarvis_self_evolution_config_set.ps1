param(
    [string]$BaseUrl = "http://127.0.0.1:8028",
    [int]$ApprovalWaitSeconds = 600,
    [int]$RunnerIdleSleepSeconds = 20,
    [double]$NightDefaultHours = 8,
    [string]$FocusDefault = "reliability"
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Body = @{
    approval_wait_seconds    = $ApprovalWaitSeconds
    runner_idle_sleep_seconds = $RunnerIdleSleepSeconds
    night_default_hours      = $NightDefaultHours
    focus_default            = $FocusDefault
} | ConvertTo-Json -Depth 20

Invoke-RestMethod `
    -Method POST `
    -Uri "$BaseUrl/api/self-evolution/config/update" `
    -ContentType "application/json" `
    -Body $Body `
    -TimeoutSec 60 | ConvertTo-Json -Depth 80