param(
    [string]$BaseUrl = "http://127.0.0.1:8010"
)

Write-Host ""
Write-Host "== Phase 16.2 Smoke Test ==" -ForegroundColor Cyan

Write-Host "`n[0] verify phase16.2 routes are loaded live" -ForegroundColor Yellow
powershell -ExecutionPolicy Bypass -File ".\verify_phase16_live.ps1" -BaseUrl $BaseUrl
if ($LASTEXITCODE -eq 2) {
    throw "Live API is unreachable. Restart the API first."
}
if ($LASTEXITCODE -ne 0) {
    throw "Phase 16.2 routes are not loaded in live runtime."
}

Write-Host "`n[1] autonomous decision health" -ForegroundColor Yellow
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/autonomous-decision/health" | ConvertTo-Json -Depth 20

Write-Host "`n[2] reconcile legacy runs" -ForegroundColor Yellow
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/autonomous-decision/reconcile-legacy" | ConvertTo-Json -Depth 20

Write-Host "`n[3] memory-first loop scenario" -ForegroundColor Yellow
$memoryReq = @{
    goal = "Create and run a mission that writes a controlled output file"
    mission_type = "artifact_generation"
    risk_level = "medium"
    has_memory = $true
    requires_tools = $true
    requires_approval = $false
    loop_enabled = $true
    loop_max_steps = 3
    context = @{
        target_path = "artifacts/output/phase16_2_memory_then_tool.txt"
        content = "phase16_2 memory then tool"
        user_intent = "generate artifact"
    }
} | ConvertTo-Json -Depth 10
$memoryDecision = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/autonomous-decision/decide" -ContentType "application/json" -Body $memoryReq
$memoryDecision | ConvertTo-Json -Depth 20

Write-Host "`n[4] execute-now memory loop" -ForegroundColor Yellow
$execBody = @{ base_url = $BaseUrl } | ConvertTo-Json
$memoryExec = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/autonomous-decision/runs/$($memoryDecision.decision_id)/execute-now" -ContentType "application/json" -Body $execBody
$memoryExec | ConvertTo-Json -Depth 20

Write-Host "`n[5] mission execution scenario" -ForegroundColor Yellow
$missionReq = @{
    goal = "Run mission execution for a demo mission"
    mission_type = ""
    risk_level = "medium"
    has_memory = $false
    requires_tools = $false
    requires_approval = $false
    loop_enabled = $false
    context = @{
        mission_id = "mission_phase16_2_demo"
    }
} | ConvertTo-Json -Depth 10
$missionDecision = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/autonomous-decision/decide" -ContentType "application/json" -Body $missionReq
$missionDecision | ConvertTo-Json -Depth 20

Write-Host "`n[6] execute-now mission run" -ForegroundColor Yellow
$missionExec = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/autonomous-decision/runs/$($missionDecision.decision_id)/execute-now" -ContentType "application/json" -Body $execBody
$missionExec | ConvertTo-Json -Depth 20

Write-Host "`n[7] list decision runs" -ForegroundColor Yellow
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/autonomous-decision/runs" | ConvertTo-Json -Depth 20

Write-Host "`n[8] check mission journal created by execution bridge" -ForegroundColor Yellow
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/execution/missions/mission_phase16_2_demo/journal" | ConvertTo-Json -Depth 20
