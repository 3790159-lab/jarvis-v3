param(
    [string]$BaseUrl = "http://127.0.0.1:8015",
    [string]$MissionId = "mission_custom_001"
)

$ErrorActionPreference = "Stop"

# Auth for the default-deny guard (audit 2026-07-15). Sends X-API-Key from env.
$ApiKey = $env:JARVIS_INTERNAL_API_KEY
if (-not $ApiKey) { $ApiKey = $env:JARVIS_ADMIN_KEY }
$AuthHeaders = @{}
if ($ApiKey) { $AuthHeaders["X-API-Key"] = $ApiKey }

Write-Host "== Health =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/health" -Headers $AuthHeaders | ConvertTo-Json -Depth 10

Write-Host "`n== Tools list =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/tools" -Headers $AuthHeaders | ConvertTo-Json -Depth 20

Write-Host "`n== Execute file_write tool =="
Invoke-RestMethod `
  -Method POST `
  -Uri "$BaseUrl/api/autonomy/tools/execute" -Headers $AuthHeaders `
  -ContentType "application/json" `
  -Body (@{
      tool_id = "file_write"
      requested_by = "smoke_test"
      payload = @{
          path = "artifacts/autonomy/tool_test.txt"
          content = "hello from tool executor`n"
          append = $false
      }
  } | ConvertTo-Json -Depth 10) | ConvertTo-Json -Depth 20

Write-Host "`n== Execute file_read tool =="
Invoke-RestMethod `
  -Method POST `
  -Uri "$BaseUrl/api/autonomy/tools/execute" -Headers $AuthHeaders `
  -ContentType "application/json" `
  -Body (@{
      tool_id = "file_read"
      requested_by = "smoke_test"
      payload = @{
          path = "artifacts/autonomy/tool_test.txt"
      }
  } | ConvertTo-Json -Depth 10) | ConvertTo-Json -Depth 20

Write-Host "`n== Set operator_assisted mode =="
Invoke-RestMethod `
  -Method POST `
  -Uri "$BaseUrl/api/autonomy/mode" -Headers $AuthHeaders `
  -ContentType "application/json" `
  -Body (@{
      mode = "operator_assisted"
      autonomous_continuation_enabled = $true
      require_approval_for_scheduled = $true
      require_approval_for_replan_followup = $true
      allow_event_driven_auto_continue = $false
      allow_manual_continue_when_paused = $false
  } | ConvertTo-Json -Depth 10) | ConvertTo-Json -Depth 20

Write-Host "`n== Schedule continuation for approval lifecycle test =="
$jobResp = Invoke-RestMethod `
  -Method POST `
  -Uri "$BaseUrl/api/autonomy/schedule" -Headers $AuthHeaders `
  -ContentType "application/json" `
  -Body (@{
      mission_id = $MissionId
      delay_seconds = 1
      reason = "stabilization_tools_smoke"
      payload = @{ source = "stabilization_tools_smoke" }
  } | ConvertTo-Json -Depth 10)

$jobResp | ConvertTo-Json -Depth 20

Start-Sleep -Seconds 7

Write-Host "`n== Pending approvals after scheduler pass =="
$approvals = Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/approvals?status=pending" -Headers $AuthHeaders
$approvals | ConvertTo-Json -Depth 20

if ($approvals.count -gt 0) {
    $approvalId = $approvals.approvals[0].approval_id
    Write-Host "`n== Approving first pending approval =="
    Invoke-RestMethod `
      -Method POST `
      -Uri "$BaseUrl/api/autonomy/approvals/$approvalId/approve" -Headers $AuthHeaders `
      -ContentType "application/json" `
      -Body (@{ note = "approved by smoke test" } | ConvertTo-Json -Depth 10) | ConvertTo-Json -Depth 20

    Start-Sleep -Seconds 7
}

Write-Host "`n== Tool executions =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/tools/executions?limit=20" -Headers $AuthHeaders | ConvertTo-Json -Depth 20

Write-Host "`n== Dashboard =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/dashboard" -Headers $AuthHeaders | ConvertTo-Json -Depth 40
