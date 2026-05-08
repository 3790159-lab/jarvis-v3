param(
    [string]$BaseUrl = "http://127.0.0.1:8010"
)

Write-Host ""
Write-Host "== Phase 13 Hardened Smoke Test ==" -ForegroundColor Cyan

Write-Host "`n[0] verify feedback routes are loaded live" -ForegroundColor Yellow
powershell -ExecutionPolicy Bypass -File ".\verify_feedback_live.ps1" -BaseUrl $BaseUrl
if ($LASTEXITCODE -ne 0) {
    throw "Feedback routes are not loaded in the live server. Restart the API first."
}

Write-Host "`n[1] feedback health" -ForegroundColor Yellow
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/feedback/health" | ConvertTo-Json -Depth 20

Write-Host "`n[2] evaluate good planner run" -ForegroundColor Yellow
$good1 = @{
    agent_id = "planner_agent_01"
    role = "planner"
    stage_success = $true
    qa_pass = $true
    retry_count = 0
} | ConvertTo-Json
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/feedback/evaluate" -ContentType "application/json" -Body $good1 | ConvertTo-Json -Depth 20

Write-Host "`n[3] evaluate weak executor run" -ForegroundColor Yellow
$bad1 = @{
    agent_id = "executor_agent_01"
    role = "executor"
    stage_success = $false
    qa_pass = $false
    retry_count = 2
} | ConvertTo-Json
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/feedback/evaluate" -ContentType "application/json" -Body $bad1 | ConvertTo-Json -Depth 20

Write-Host "`n[4] evaluate second good planner run" -ForegroundColor Yellow
$good2 = @{
    agent_id = "planner_agent_01"
    role = "planner"
    stage_success = $true
    qa_pass = $true
    retry_count = 0
} | ConvertTo-Json
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/feedback/evaluate" -ContentType "application/json" -Body $good2 | ConvertTo-Json -Depth 20

Write-Host "`n[5] list agents" -ForegroundColor Yellow
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/feedback/agents" | ConvertTo-Json -Depth 20

Write-Host "`n[6] get planner_agent_01" -ForegroundColor Yellow
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/feedback/agents/planner_agent_01" | ConvertTo-Json -Depth 20
