param(
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

function Show-Json {
    param($Object)
    $Object | ConvertTo-Json -Depth 100
}

Write-Host ""
Write-Host "==================== /api/agent-mesh/health ====================" -ForegroundColor Cyan
$Health = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/agent-mesh/health"
Show-Json $Health

Write-Host ""
Write-Host "==================== /api/agent-mesh/learning/skills ====================" -ForegroundColor Cyan
$Skills = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/agent-mesh/learning/skills?limit=20"
Show-Json $Skills

Write-Host ""
Write-Host "==================== /api/agent-mesh/learning/lessons ====================" -ForegroundColor Cyan
$Lessons = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/agent-mesh/learning/lessons?limit=20"
Show-Json $Lessons

Write-Host ""
Write-Host "==================== /api/agent-mesh/learning/guidance/preview ====================" -ForegroundColor Cyan
$Guidance = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/agent-mesh/learning/guidance/preview" `
    -ContentType "application/json; charset=utf-8" `
    -Body (@{
        required_capability = "codegen"
        task_type = "integration"
        title = "Preview learned guidance for Claude check"
        priority = "high"
        risk_level = "normal"
        preferred_service = "claude_bridge"
        connector_action = "config_check"
        dry_run = $false
        agent_id = "coding_agent_main"
    } | ConvertTo-Json -Depth 20)
Show-Json $Guidance

Write-Host ""
Write-Host "==================== /api/agent-mesh/runtime/integration-task/execute (claude/native v10) ====================" -ForegroundColor Cyan
$NativeClaude = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/agent-mesh/runtime/integration-task/execute" `
    -ContentType "application/json; charset=utf-8" `
    -Body (@{
        task_id = "native_claude_check_api_v10"
        title = "Native Claude integration check V10"
        required_capability = "codegen"
        preferred_service = "claude_bridge"
        connector_action = "config_check"
        dry_run = $false
        connector_payload = @{}
        priority = "high"
    } | ConvertTo-Json -Depth 20)
Show-Json $NativeClaude

Write-Host ""
Write-Host "==================== /api/agent-mesh/demo-run-v10 ====================" -ForegroundColor Cyan
$Demo = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/agent-mesh/demo-run-v10" `
    -ContentType "application/json; charset=utf-8" `
    -Body (@{ goal = "Run API v10 skill-memory smoke demo." } | ConvertTo-Json -Depth 20)
Show-Json $Demo