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
Write-Host "==================== /api/agent-mesh/knowledge/ingest ====================" -ForegroundColor Cyan
$Knowledge = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/agent-mesh/knowledge/ingest" `
    -ContentType "application/json; charset=utf-8" `
    -Body (@{
        title = "How to communicate with agents and use cloud tools"
        text = @"
When coordinating agents, keep roles explicit and avoid over-consultation.
Use second opinions mainly for risky tasks.
Claude is usually strongest for concrete engineering and refactor work.
OpenAI can remain strong for planning and QA framing.
n8n should stay guarded until auth and live webhook configuration are verified.
Knowledge should be organized by domain so programming guidance, communication rules, and integration patterns do not collapse into one flat memory.
"@
        source_type = "lecture"
        category = "agent_guidance"
        auto_apply = $true
    } | ConvertTo-Json -Depth 20)
Show-Json $Knowledge

Write-Host ""
Write-Host "==================== /api/agent-mesh/knowledge/library ====================" -ForegroundColor Cyan
$Library = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/agent-mesh/knowledge/library?limit=10"
Show-Json $Library

Write-Host ""
Write-Host "==================== /api/agent-mesh/knowledge/domains ====================" -ForegroundColor Cyan
$Domains = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/agent-mesh/knowledge/domains?include_items=true"
Show-Json $Domains

Write-Host ""
Write-Host "==================== /api/agent-mesh/learning/guidance/preview ====================" -ForegroundColor Cyan
$Guidance = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/agent-mesh/learning/guidance/preview" `
    -ContentType "application/json; charset=utf-8" `
    -Body (@{
        required_capability = "workflow_run"
        task_type = "integration"
        title = "Preview learned guidance for n8n workflow"
        priority = "high"
        risk_level = "normal"
        preferred_service = "n8n"
        connector_action = "webhook_invoke"
        dry_run = $true
        agent_id = "n8n_agent_main"
    } | ConvertTo-Json -Depth 20)
Show-Json $Guidance

Write-Host ""
Write-Host "==================== /api/agent-mesh/demo-run-v10 ====================" -ForegroundColor Cyan
$Demo = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/agent-mesh/demo-run-v10" `
    -ContentType "application/json; charset=utf-8" `
    -Body (@{ goal = "Run API V12 stabilization + domains smoke demo." } | ConvertTo-Json -Depth 20)
Show-Json $Demo

Write-Host ""
Write-Host "==================== /api/agent-mesh/lifecycle/cleanup ====================" -ForegroundColor Cyan
$Cleanup = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/agent-mesh/lifecycle/cleanup?retain_recent_completed=10&max_journal_lines_per_mission=400&max_exec_log_lines=600"
Show-Json $Cleanup