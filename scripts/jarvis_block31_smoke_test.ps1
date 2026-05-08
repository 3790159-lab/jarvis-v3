param(
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

$ErrorActionPreference = "Stop"

function Invoke-JsonRequest {
    param(
        [Parameter(Mandatory=$true)][string]$Method,
        [Parameter(Mandatory=$true)][string]$Uri,
        [object]$Body = $null
    )

    if ($null -eq $Body) {
        return Invoke-RestMethod -Method $Method -Uri $Uri -TimeoutSec 900
    }

    $json = $Body | ConvertTo-Json -Depth 80
    return Invoke-RestMethod -Method $Method -Uri $Uri -ContentType "application/json" -Body $json -TimeoutSec 900
}

Write-Host "== Control health ==" -ForegroundColor Cyan
$health = Invoke-JsonRequest -Method "GET" -Uri "$BaseUrl/api/control/health"
$health | ConvertTo-Json -Depth 20

Write-Host "== Adapters ==" -ForegroundColor Cyan
$adapters = Invoke-JsonRequest -Method "GET" -Uri "$BaseUrl/api/agents/adapters"
$adapters | ConvertTo-Json -Depth 20

Write-Host "== Register agent ==" -ForegroundColor Cyan
$agent = Invoke-JsonRequest -Method "POST" -Uri "$BaseUrl/api/agents/register" -Body @{
    agent_id = "agent_local_echo_01"
    display_name = "Local Echo Agent"
    adapter_name = "local_echo"
    capabilities = @("chat","code","reasoning")
    enabled = $true
    weight = 1.0
    metadata = @{
        role = "test_agent"
    }
}
$agent | ConvertTo-Json -Depth 20

Write-Host "== Create low-risk task ==" -ForegroundColor Cyan
$task = Invoke-JsonRequest -Method "POST" -Uri "$BaseUrl/api/agents/tasks" -Body @{
    title = "Draft a safe local response"
    description = "Simple local echo task"
    payload = @{
        prompt = "Hello from task marketplace"
    }
    required_capabilities = @("chat")
    requested_tools = @()
    metadata = @{
        adapter_name = "local_echo"
    }
}
$task | ConvertTo-Json -Depth 20

Write-Host "== Lease task ==" -ForegroundColor Cyan
$leased = Invoke-JsonRequest -Method "POST" -Uri "$BaseUrl/api/agents/tasks/lease" -Body @{
    agent_id = "agent_local_echo_01"
}
$leased | ConvertTo-Json -Depth 20

if ($leased.task -and $leased.task.task_id) {
    Write-Host "== Complete task ==" -ForegroundColor Cyan
    $completed = Invoke-JsonRequest -Method "POST" -Uri "$BaseUrl/api/agents/tasks/$($leased.task.task_id)/complete" -Body @{
        result = @{
            text = "Task completed by smoke test"
        }
    }
    $completed | ConvertTo-Json -Depth 20
}

Write-Host "== Risky invoke triggers approval ==" -ForegroundColor Cyan
$risky = Invoke-JsonRequest -Method "POST" -Uri "$BaseUrl/api/agents/invoke" -Body @{
    adapter_name = "claude_code_bridge"
    payload = @{
        prompt = "Analyze a repository and propose changes"
    }
    requested_capabilities = @("code","external_ai")
    requested_tools = @("shell")
    mission_id = "mission_demo_external"
    step_id = "step_external_agent"
}
$risky | ConvertTo-Json -Depth 20

$approvalId = $null
if ($risky.approval -and $risky.approval.approval_id) {
    $approvalId = $risky.approval.approval_id

    Write-Host "== Approve request ==" -ForegroundColor Cyan
    $approved = Invoke-JsonRequest -Method "POST" -Uri "$BaseUrl/api/approvals/$approvalId/approve" -Body @{
        operator_note = "Approved for smoke flow"
    }
    $approved | ConvertTo-Json -Depth 20
}

Write-Host "== Safe adapter invoke ==" -ForegroundColor Cyan
$invoke = Invoke-JsonRequest -Method "POST" -Uri "$BaseUrl/api/agents/invoke" -Body @{
    adapter_name = "local_echo"
    payload = @{
        prompt = "Hello from adapter invoke"
    }
    requested_capabilities = @("chat")
}
$invoke | ConvertTo-Json -Depth 20

Write-Host "== Remember memory ==" -ForegroundColor Cyan
$mem = Invoke-JsonRequest -Method "POST" -Uri "$BaseUrl/api/memory/remember" -Body @{
    kind = "note"
    title = "Smoke test note"
    text_body = "Jarvis agent control plane smoke test completed."
    tags = @("smoke","control_plane")
    source = "jarvis_block31_smoke_test"
}
$mem | ConvertTo-Json -Depth 20

Write-Host "== Ingest latest packaged mission if available ==" -ForegroundColor Cyan
$artifactMissions = Invoke-JsonRequest -Method "GET" -Uri "$BaseUrl/api/artifacts/missions"
$artifactMissions | ConvertTo-Json -Depth 20

if ($artifactMissions.items -and $artifactMissions.items.Count -gt 0) {
    $latestMissionId = $artifactMissions.items[0].mission_id
    $ingest = Invoke-JsonRequest -Method "POST" -Uri "$BaseUrl/api/memory/ingest-mission/$latestMissionId"
    $ingest | ConvertTo-Json -Depth 30
}

Write-Host "== Search memory ==" -ForegroundColor Cyan
$search = Invoke-JsonRequest -Method "GET" -Uri "$BaseUrl/api/memory/search?q=smoke&limit=10"
$search | ConvertTo-Json -Depth 30

Write-Host "== Recent approvals ==" -ForegroundColor Cyan
$approvals = Invoke-JsonRequest -Method "GET" -Uri "$BaseUrl/api/approvals"
$approvals | ConvertTo-Json -Depth 30

Write-Host "Block 3 foundation smoke test finished." -ForegroundColor Green