param(
    [string]$ProjectRoot = (Get-Location).Path,
    [string]$Action = "echo",
    [string]$Intent = "automation",
    [string]$MissionId = ("manual-" + [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()),
    [string]$TaskId = "task-1",
    [string]$PayloadJson = '{"message":"hello from jarvis"}',
    [switch]$UseTestWebhook
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. "$PSScriptRoot\jarvis_n8n_common.ps1"
$ctx = Get-JarvisContext -ProjectRoot $ProjectRoot

if (-not $ctx.Env.ContainsKey("JARVIS_N8N_BASE_URL")) {
    throw "JARVIS_N8N_BASE_URL not found in .env"
}
if (-not $ctx.Env.ContainsKey("JARVIS_N8N_TRIGGER_PATH")) {
    throw "JARVIS_N8N_TRIGGER_PATH not found in .env"
}

$baseUrl = $ctx.Env["JARVIS_N8N_BASE_URL"].TrimEnd("/")

if ($UseTestWebhook) {
    if ($ctx.Env.ContainsKey("JARVIS_N8N_TEST_TRIGGER_PATH")) {
        $path = $ctx.Env["JARVIS_N8N_TEST_TRIGGER_PATH"]
    } else {
        $path = "/webhook-test/jarvis/inbox"
    }
}
else {
    $path = $ctx.Env["JARVIS_N8N_TRIGGER_PATH"]
}

$uri = "$baseUrl$path"

try {
    $payloadObj = $PayloadJson | ConvertFrom-Json
} catch {
    throw "PayloadJson is not valid JSON: $($_.Exception.Message)"
}

$body = [ordered]@{
    source     = "jarvis"
    mission_id = $MissionId
    task_id    = $TaskId
    intent     = $Intent
    action     = $Action
    created_at = (Get-Date).ToString("o")
    payload    = $payloadObj
} | ConvertTo-Json -Depth 50

$headers = @{
    "X-Jarvis-Source"     = "jarvis"
    "X-Jarvis-Mission-Id" = $MissionId
    "X-Jarvis-Task-Id"    = $TaskId
}

if ($ctx.Env.ContainsKey("JARVIS_N8N_SHARED_KEY") -and -not [string]::IsNullOrWhiteSpace($ctx.Env["JARVIS_N8N_SHARED_KEY"])) {
    $headers["X-Jarvis-Key"] = $ctx.Env["JARVIS_N8N_SHARED_KEY"]
}

Invoke-RestMethod `
    -Method POST `
    -Uri $uri `
    -Headers $headers `
    -ContentType "application/json" `
    -Body $body | ConvertTo-Json -Depth 20