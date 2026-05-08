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
        title = "How to govern memory and service routing"
        text = @"
Claude is usually strongest for concrete engineering, refactor and implementation work when prompts are explicit.
OpenAI can remain strong for planning, review and QA framing.
Avoid promoting n8n to broad live execution until auth and webhook verification are complete.
Keep communication between agents structured: explicit roles, limited consultation, second opinions mostly for risky tasks.
Knowledge should be separated by domain and block, but linked logically across engineering, planning, communication, memory and integration patterns.
"@
        source_type = "best_practice"
        category = "agent_guidance"
        auto_apply = $true
    } | ConvertTo-Json -Depth 20)
Show-Json $Knowledge

Write-Host ""
Write-Host "==================== /api/agent-mesh/knowledge/domains ====================" -ForegroundColor Cyan
$Domains = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/agent-mesh/knowledge/domains?include_items=true"
Show-Json $Domains

Write-Host ""
Write-Host "==================== /api/agent-mesh/knowledge/review-queue ====================" -ForegroundColor Cyan
$Review = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/agent-mesh/knowledge/review-queue?limit=20"
Show-Json $Review

Write-Host ""
Write-Host "==================== /api/agent-mesh/knowledge/negative-rules ====================" -ForegroundColor Cyan
$Negative = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/agent-mesh/knowledge/negative-rules?limit=20"
Show-Json $Negative

Write-Host ""
Write-Host "==================== /api/agent-mesh/service-telemetry ====================" -ForegroundColor Cyan
$Telemetry = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/agent-mesh/service-telemetry"
Show-Json $Telemetry

Write-Host ""
Write-Host "==================== /api/agent-mesh/service-traces ====================" -ForegroundColor Cyan
$Traces = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/agent-mesh/service-traces?limit=20"
Show-Json $Traces

Write-Host ""
Write-Host "==================== /api/agent-mesh/communication-policy ====================" -ForegroundColor Cyan
$CommPolicy = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/agent-mesh/communication-policy"
Show-Json $CommPolicy

Write-Host ""
Write-Host "==================== /api/agent-mesh/demo-run-v10 ====================" -ForegroundColor Cyan
$Demo = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/agent-mesh/demo-run-v10" `
    -ContentType "application/json; charset=utf-8" `
    -Body (@{ goal = "Run API V13 memory mesh + service intelligence smoke demo." } | ConvertTo-Json -Depth 20)
Show-Json $Demo

Write-Host ""
Write-Host "==================== /api/agent-mesh/lifecycle/cleanup ====================" -ForegroundColor Cyan
$Cleanup = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/agent-mesh/lifecycle/cleanup?retain_recent_completed=10&max_journal_lines_per_mission=400&max_exec_log_lines=600"
Show-Json $Cleanup