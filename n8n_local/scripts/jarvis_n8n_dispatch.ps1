param(
    [string]$ProjectRoot = (Get-Location).Path,
    [string]$Action = "echo",
    [string]$Intent = "automation",
    [string]$MissionId = ("manual-" + [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()),
    [string]$TaskId = "task-1",
    [string]$PayloadJson = '{"message":"hello from jarvis"}',
    [switch]$UseTestWebhook
)
. "$PSScriptRoot\jarvis_n8n_common.ps1"
$ctx = Get-JarvisN8nContext -ProjectRoot $ProjectRoot

if (-not $ctx.ProjectEnv.ContainsKey("JARVIS_N8N_BASE_URL")) { throw "JARVIS_N8N_BASE_URL отсутствует в .env" }
if (-not $ctx.ProjectEnv.ContainsKey("JARVIS_N8N_SHARED_KEY")) { throw "JARVIS_N8N_SHARED_KEY отсутствует в .env" }

$baseUrl = $ctx.ProjectEnv["JARVIS_N8N_BASE_URL"].TrimEnd("/")
$path = if ($UseTestWebhook) { "/webhook-test/jarvis/inbox" } else { "/webhook/jarvis/inbox" }
$uri = "$baseUrl$path"
$sharedKey = $ctx.ProjectEnv["JARVIS_N8N_SHARED_KEY"]

$payloadObj = $PayloadJson | ConvertFrom-Json -Depth 100
$body = [ordered]@{
    source     = "jarvis"
    mission_id = $MissionId
    task_id    = $TaskId
    intent     = $Intent
    action     = $Action
    created_at = (Get-Date).ToString("o")
    payload    = $payloadObj
} | ConvertTo-Json -Depth 50

Invoke-RestMethod `
    -Method POST `
    -Uri $uri `
    -Headers @{
        "X-Jarvis-Key"       = $sharedKey
        "X-Jarvis-Source"    = "jarvis"
        "X-Jarvis-Mission-Id"= $MissionId
        "X-Jarvis-Task-Id"   = $TaskId
    } `
    -ContentType "application/json" `
    -Body $body