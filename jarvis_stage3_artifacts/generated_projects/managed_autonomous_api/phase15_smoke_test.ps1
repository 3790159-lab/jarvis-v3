param(
    [string]$BaseUrl = "http://127.0.0.1:8010"
)

Write-Host ""
Write-Host "== Phase 15 Hardened Smoke Test ==" -ForegroundColor Cyan

Write-Host "`n[0] verify phase15 routes are loaded live" -ForegroundColor Yellow
powershell -ExecutionPolicy Bypass -File ".\verify_phase15_live.ps1" -BaseUrl $BaseUrl
if ($LASTEXITCODE -eq 2) {
    throw "Live API is unreachable. Restart the API first."
}
if ($LASTEXITCODE -ne 0) {
    throw "Phase 15 routes are not loaded in live runtime."
}

Write-Host "`n[1] hitl health" -ForegroundColor Yellow
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/hitl/health" | ConvertTo-Json -Depth 20

Write-Host "`n[2] create approval" -ForegroundColor Yellow
$createBody = @{
    mission_id = "mission_demo_approval_001"
    action_type = "filesystem_write"
    proposed_action = "Write final export file to artifacts/output/report.txt"
    reason = "Mission requires final output artifact"
    risk_level = "medium"
    agent_id = "executor_agent_01"
    agent_score = 72.5
    payload = @{
        path = "artifacts/output/report.txt"
        size_estimate = "small"
    }
} | ConvertTo-Json -Depth 10
$created = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/hitl/approvals" -ContentType "application/json" -Body $createBody
$created | ConvertTo-Json -Depth 20

$approvalId = $created.approval_id

Write-Host "`n[3] create duplicate approval (should reuse pending)" -ForegroundColor Yellow
$duplicate = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/hitl/approvals" -ContentType "application/json" -Body $createBody
$duplicate | ConvertTo-Json -Depth 20

Write-Host "`n[4] list pending approvals" -ForegroundColor Yellow
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/hitl/approvals?status=pending" | ConvertTo-Json -Depth 20

Write-Host "`n[5] get approval by id" -ForegroundColor Yellow
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/hitl/approvals/$approvalId" | ConvertTo-Json -Depth 20

Write-Host "`n[6] approve request" -ForegroundColor Yellow
$decisionBody = @{
    decision = "approve"
    operator_note = "Approved for controlled artifact output"
} | ConvertTo-Json
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/hitl/approvals/$approvalId/decision" -ContentType "application/json" -Body $decisionBody | ConvertTo-Json -Depth 20

Write-Host "`n[7] list all approvals" -ForegroundColor Yellow
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/hitl/approvals" | ConvertTo-Json -Depth 20
