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

Write-Host "`n== Cleanup legacy pending jobs =="
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/autonomy/maintenance/cleanup-legacy-pending" -Headers $AuthHeaders | ConvertTo-Json -Depth 20

Write-Host "`n== Cleanup orphan approvals =="
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/autonomy/maintenance/cleanup-orphan-approvals" -Headers $AuthHeaders | ConvertTo-Json -Depth 20

Write-Host "`n== Normalize jobs and approvals =="
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/autonomy/maintenance/normalize" -Headers $AuthHeaders | ConvertTo-Json -Depth 20

Write-Host "`n== Try auto-unpause =="
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/autonomy/maintenance/auto-unpause/$MissionId" -Headers $AuthHeaders | ConvertTo-Json -Depth 20

Write-Host "`n== Chain templates =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/chains" -Headers $AuthHeaders | ConvertTo-Json -Depth 20

Write-Host "`n== Execute write_append_read chain =="
Invoke-RestMethod `
  -Method POST `
  -Uri "$BaseUrl/api/autonomy/chains/execute" -Headers $AuthHeaders `
  -ContentType "application/json" `
  -Body (@{
      chain_id = "write_append_read"
      requested_by = "advanced_tools_smoke"
      payload = @{
          path = "artifacts/autonomy/chain_test.txt"
          initial_content = "alpha`n"
          append_content = "beta`n"
      }
  } | ConvertTo-Json -Depth 20) | ConvertTo-Json -Depth 40

Write-Host "`n== Execute file patch tool =="
Invoke-RestMethod `
  -Method POST `
  -Uri "$BaseUrl/api/autonomy/tools/execute" -Headers $AuthHeaders `
  -ContentType "application/json" `
  -Body (@{
      tool_id = "file_patch_text"
      requested_by = "advanced_tools_smoke"
      payload = @{
          path = "artifacts/autonomy/chain_test.txt"
          search = "beta"
          replace = "gamma"
      }
  } | ConvertTo-Json -Depth 20) | ConvertTo-Json -Depth 40

Write-Host "`n== Read patched file =="
Invoke-RestMethod `
  -Method POST `
  -Uri "$BaseUrl/api/autonomy/tools/execute" -Headers $AuthHeaders `
  -ContentType "application/json" `
  -Body (@{
      tool_id = "file_read"
      requested_by = "advanced_tools_smoke"
      payload = @{
          path = "artifacts/autonomy/chain_test.txt"
      }
  } | ConvertTo-Json -Depth 20) | ConvertTo-Json -Depth 40

Write-Host "`n== Dashboard =="
Invoke-RestMethod -Uri "$BaseUrl/api/autonomy/dashboard" -Headers $AuthHeaders | ConvertTo-Json -Depth 50
