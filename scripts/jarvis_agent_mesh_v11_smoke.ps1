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
Write-Host "==================== /api/agent-mesh/lifecycle/health ====================" -ForegroundColor Cyan
$Lifecycle = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/agent-mesh/lifecycle/health"
Show-Json $Lifecycle

Write-Host ""
Write-Host "==================== /api/agent-mesh/knowledge/ingest ====================" -ForegroundColor Cyan
$Knowledge = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/agent-mesh/knowledge/ingest" `
    -ContentType "application/json; charset=utf-8" `
    -Body (@{
        title = "How to work with Claude and integrations"
        text = @"
Claude is often strongest for code drafting, refactors and precise implementation work when prompts are explicit.
OpenAI can remain strong for structured planning and QA framing.
n8n should only be promoted to broader live execution after configuration and auth are fully verified.
Memory/context support should stay enabled so the system can reuse prior lessons and patterns.
"@
        source_type = "article"
        category = "agent_guidance"
        auto_apply = $true
    } | ConvertTo-Json -Depth 20)
Show-Json $Knowledge

Write-Host ""
Write-Host "==================== /api/agent-mesh/knowledge/library ====================" -ForegroundColor Cyan
$Library = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/agent-mesh/knowledge/library?limit=10"
Show-Json $Library

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
Write-Host "==================== /api/agent-mesh/runtime/integration-task/execute (claude/native v11) ====================" -ForegroundColor Cyan
$NativeClaude = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/agent-mesh/runtime/integration-task/execute" `
    -ContentType "application/json; charset=utf-8" `
    -Body (@{
        task_id = "native_claude_check_api_v11"
        title = "Native Claude integration check V11"
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
    -Body (@{ goal = "Run API V11 stabilization smoke demo." } | ConvertTo-Json -Depth 20)
Show-Json $Demo

Write-Host ""
Write-Host "==================== /api/agent-mesh/lifecycle/cleanup ====================" -ForegroundColor Cyan
$Cleanup = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/agent-mesh/lifecycle/cleanup?retain_recent_completed=10&max_journal_lines_per_mission=400&max_exec_log_lines=600"
Show-Json $Cleanup