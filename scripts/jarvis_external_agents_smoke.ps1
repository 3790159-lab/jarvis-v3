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

    $json = $Body | ConvertTo-Json -Depth 100
    return Invoke-RestMethod -Method $Method -Uri $Uri -ContentType "application/json" -Body $json -TimeoutSec 900
}

Write-Host "== Control health ==" -ForegroundColor Cyan
$health = Invoke-JsonRequest -Method "GET" -Uri "$BaseUrl/api/control/health"
$health | ConvertTo-Json -Depth 20

Write-Host "== Adapters ==" -ForegroundColor Cyan
$adapters = Invoke-JsonRequest -Method "GET" -Uri "$BaseUrl/api/agents/adapters"
$adapters | ConvertTo-Json -Depth 30

$AdapterNames = @{}
foreach ($item in ($adapters.items | Where-Object { $_ })) {
    $AdapterNames[$item.name] = $item
}

if ($AdapterNames.ContainsKey("claude_code_bridge") -and $AdapterNames["claude_code_bridge"].enabled) {
    Write-Host "== Register Claude agent ==" -ForegroundColor Cyan
    $claudeAgent = Invoke-JsonRequest -Method "POST" -Uri "$BaseUrl/api/agents/register" -Body @{
        agent_id = "agent_claude_code_01"
        display_name = "Claude Code Agent"
        adapter_name = "claude_code_bridge"
        capabilities = @("code","reasoning","external_ai")
        enabled = $true
        weight = 1.0
        metadata = @{
            role = "external_coding_agent"
        }
    }
    $claudeAgent | ConvertTo-Json -Depth 20

    Write-Host "== Claude invoke (approval flow) ==" -ForegroundColor Cyan
    $claudeRequest = @{
        adapter_name = "claude_code_bridge"
        payload = @{
            prompt = "Reply with exactly: CLAUDE_BRIDGE_OK"
        }
        requested_capabilities = @("code","external_ai")
        requested_tools = @("shell")
        mission_id = "mission_external_claude"
        step_id = "step_claude_bridge"
    }

    $claudeInvoke = Invoke-JsonRequest -Method "POST" -Uri "$BaseUrl/api/agents/invoke" -Body $claudeRequest
    $claudeInvoke | ConvertTo-Json -Depth 30

    if ($claudeInvoke.status -eq "approval_required" -and $claudeInvoke.approval.approval_id) {
        $approvalId = $claudeInvoke.approval.approval_id

        Write-Host "== Approve Claude invoke ==" -ForegroundColor Cyan
        $approved = Invoke-JsonRequest -Method "POST" -Uri "$BaseUrl/api/approvals/$approvalId/approve" -Body @{
            operator_note = "Approved for Claude bridge smoke test"
        }
        $approved | ConvertTo-Json -Depth 20

        Write-Host "== Claude invoke after approval ==" -ForegroundColor Cyan
        $claudeInvoke2 = Invoke-JsonRequest -Method "POST" -Uri "$BaseUrl/api/agents/invoke" -Body @{
            adapter_name = "claude_code_bridge"
            approval_id = $approvalId
            payload = @{
                prompt = "Reply with exactly: CLAUDE_BRIDGE_OK"
            }
            requested_capabilities = @("code","external_ai")
            requested_tools = @("shell")
            mission_id = "mission_external_claude"
            step_id = "step_claude_bridge"
        }
        $claudeInvoke2 | ConvertTo-Json -Depth 40
    }
} else {
    Write-Host "Claude bridge is disabled. Skipping Claude adapter test." -ForegroundColor Yellow
}

if ($AdapterNames.ContainsKey("openai_compatible_http") -and $AdapterNames["openai_compatible_http"].enabled) {
    Write-Host "== Register OpenAI-compatible agent ==" -ForegroundColor Cyan
    $openaiAgent = Invoke-JsonRequest -Method "POST" -Uri "$BaseUrl/api/agents/register" -Body @{
        agent_id = "agent_openai_remote_01"
        display_name = "OpenAI-Compatible Remote Agent"
        adapter_name = "openai_compatible_http"
        capabilities = @("chat","reasoning","code","remote_ai","external_ai")
        enabled = $true
        weight = 1.0
        metadata = @{
            role = "external_remote_agent"
        }
    }
    $openaiAgent | ConvertTo-Json -Depth 20

    Write-Host "== OpenAI-compatible invoke (approval flow) ==" -ForegroundColor Cyan
    $openaiRequest = @{
        adapter_name = "openai_compatible_http"
        payload = @{
            prompt = "Reply with exactly: OPENAI_COMPAT_OK"
        }
        requested_capabilities = @("chat","external_ai")
        requested_tools = @()
        mission_id = "mission_external_openai"
        step_id = "step_openai_bridge"
    }

    $openaiInvoke = Invoke-JsonRequest -Method "POST" -Uri "$BaseUrl/api/agents/invoke" -Body $openaiRequest
    $openaiInvoke | ConvertTo-Json -Depth 30

    if ($openaiInvoke.status -eq "approval_required" -and $openaiInvoke.approval.approval_id) {
        $approvalId2 = $openaiInvoke.approval.approval_id

        Write-Host "== Approve OpenAI-compatible invoke ==" -ForegroundColor Cyan
        $approved2 = Invoke-JsonRequest -Method "POST" -Uri "$BaseUrl/api/approvals/$approvalId2/approve" -Body @{
            operator_note = "Approved for OpenAI-compatible bridge smoke test"
        }
        $approved2 | ConvertTo-Json -Depth 20

        Write-Host "== OpenAI-compatible invoke after approval ==" -ForegroundColor Cyan
        $openaiInvoke2 = Invoke-JsonRequest -Method "POST" -Uri "$BaseUrl/api/agents/invoke" -Body @{
            adapter_name = "openai_compatible_http"
            approval_id = $approvalId2
            payload = @{
                prompt = "Reply with exactly: OPENAI_COMPAT_OK"
            }
            requested_capabilities = @("chat","external_ai")
            requested_tools = @()
            mission_id = "mission_external_openai"
            step_id = "step_openai_bridge"
        }
        $openaiInvoke2 | ConvertTo-Json -Depth 40
    }
} else {
    Write-Host "OpenAI-compatible bridge is disabled. Skipping remote HTTP adapter test." -ForegroundColor Yellow
}

Write-Host "== Agents registry ==" -ForegroundColor Cyan
$agents = Invoke-JsonRequest -Method "GET" -Uri "$BaseUrl/api/agents/registry"
$agents | ConvertTo-Json -Depth 30

Write-Host "External agents smoke test finished." -ForegroundColor Green