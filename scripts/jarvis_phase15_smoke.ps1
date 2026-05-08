param(
    [string]$BaseUrl = "http://127.0.0.1:8015",
    [string]$MissionId = "mission_custom_001"
)

$ErrorActionPreference = "Continue"

Write-Host "== Check health =="
try {
    Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/health" | ConvertTo-Json -Depth 10
} catch {
    Write-Host "Health check failed: $($_.Exception.Message)"
}

Write-Host "`n== Cleanup wrong synthetic mission from previous smoke =="
try {
    Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/autonomy/admin/delete-mission/=phase13_14_smoke" | ConvertTo-Json -Depth 10
} catch {
    Write-Host "Cleanup call failed: $($_.Exception.Message)"
}

Write-Host "`n== Create correct snapshot with JSON body =="
try {
    Invoke-RestMethod `
      -Method POST `
      -Uri "$BaseUrl/api/autonomy/memory/snapshot/$MissionId" `
      -ContentType "application/json" `
      -Body (@{
          trigger = "phase15_corrected_snapshot"
      } | ConvertTo-Json -Depth 10) | ConvertTo-Json -Depth 20
} catch {
    Write-Host "Snapshot creation failed: $($_.Exception.Message)"
}

Write-Host "`n== Resume bundle after corrected snapshot =="
try {
    Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/memory/resume-bundle/$MissionId" | ConvertTo-Json -Depth 20
} catch {
    Write-Host "Resume bundle failed: $($_.Exception.Message)"
}

Write-Host "`n== Current mode =="
try {
    Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/mode" | ConvertTo-Json -Depth 10
} catch {
    Write-Host "Current mode failed: $($_.Exception.Message)"
}

Write-Host "`n== Set mode to operator_assisted =="
try {
    Invoke-RestMethod `
      -Method POST `
      -Uri "$BaseUrl/api/autonomy/mode" `
      -ContentType "application/json" `
      -Body (@{
          mode = "operator_assisted"
          autonomous_continuation_enabled = $true
          require_approval_for_scheduled = $true
          require_approval_for_replan_followup = $true
          allow_event_driven_auto_continue = $false
          allow_manual_continue_when_paused = $false
      } | ConvertTo-Json -Depth 10) | ConvertTo-Json -Depth 20
} catch {
    Write-Host "Mode update failed: $($_.Exception.Message)"
}

Write-Host "`n== Schedule a continuation to trigger approval flow =="
try {
    Invoke-RestMethod `
      -Method POST `
      -Uri "$BaseUrl/api/autonomy/schedule" `
      -ContentType "application/json" `
      -Body (@{
          mission_id = $MissionId
          delay_seconds = 1
          reason = "phase15_operator_assisted_test"
          payload = @{ source = "phase15_smoke" }
      } | ConvertTo-Json -Depth 10) | ConvertTo-Json -Depth 20
} catch {
    Write-Host "Scheduling failed: $($_.Exception.Message)"
}

Start-Sleep -Seconds 7

Write-Host "`n== Pending approvals =="
try {
    Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/approvals?status=pending" | ConvertTo-Json -Depth 20
} catch {
    Write-Host "Pending approvals failed: $($_.Exception.Message)"
}

Write-Host "`n== Dashboard =="
try {
    Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/dashboard" | ConvertTo-Json -Depth 30
} catch {
    Write-Host "Dashboard failed: $($_.Exception.Message)"
}
