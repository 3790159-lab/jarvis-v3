Set-StrictMode -Version Latest

function Resolve-ProjectRoot {
    param(
        [string]$FallbackPath = ""
    )

    if (-not [string]::IsNullOrWhiteSpace($FallbackPath) -and (Test-Path $FallbackPath)) {
        return (Resolve-Path $FallbackPath).Path
    }

    if ($script:PSScriptRoot -and (Test-Path $script:PSScriptRoot)) {
        return (Resolve-Path (Split-Path -Parent $script:PSScriptRoot)).Path
    }

    if ($PSScriptRoot -and (Test-Path $PSScriptRoot)) {
        return (Resolve-Path (Split-Path -Parent $PSScriptRoot)).Path
    }

    if ($MyInvocation.MyCommand.Path -and (Test-Path $MyInvocation.MyCommand.Path)) {
        return (Resolve-Path (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))).Path
    }

    return (Get-Location).Path
}

function Invoke-JsonRequest {
    param(
        [Parameter(Mandatory=$true)][string]$Method,
        [Parameter(Mandatory=$true)][string]$Uri,
        [object]$Body = $null,
        [int]$TimeoutSec = 900
    )

    if ($null -eq $Body) {
        return Invoke-RestMethod -Method $Method -Uri $Uri -TimeoutSec $TimeoutSec
    }

    $json = $Body | ConvertTo-Json -Depth 100
    return Invoke-RestMethod -Method $Method -Uri $Uri -ContentType "application/json" -Body $json -TimeoutSec $TimeoutSec
}

function Save-Utf8TextFile {
    param(
        [Parameter(Mandatory=$true)][string]$Path,
        [Parameter(Mandatory=$true)][string]$Text
    )

    $Dir = Split-Path $Path -Parent
    if (-not (Test-Path $Dir)) {
        New-Item -ItemType Directory -Path $Dir -Force | Out-Null
    }

    [System.IO.File]::WriteAllText($Path, $Text, [System.Text.UTF8Encoding]::new($false))
}

function Assert-NonEmptyText {
    param(
        [AllowNull()][string]$Text,
        [Parameter(Mandatory=$true)][string]$ErrorMessage
    )

    if ([string]::IsNullOrWhiteSpace($Text)) {
        throw $ErrorMessage
    }
}

function Show-JsonDebug {
    param(
        [Parameter(Mandatory=$true)][object]$Value,
        [int]$Depth = 40
    )

    $json = $Value | ConvertTo-Json -Depth $Depth
    Write-Host $json
}

function Invoke-AgentWithApproval {
    param(
        [Parameter(Mandatory=$true)][string]$BaseUrl,
        [Parameter(Mandatory=$true)][string]$AdapterName,
        [Parameter(Mandatory=$true)][hashtable]$Payload,
        [Parameter(Mandatory=$true)][string[]]$RequestedCapabilities,
        [AllowEmptyCollection()][string[]]$RequestedTools = @(),
        [Parameter(Mandatory=$true)][string]$MissionId,
        [Parameter(Mandatory=$true)][string]$StepId,
        [Parameter(Mandatory=$true)][string]$ApprovalNote
    )

    if ($null -eq $RequestedTools) {
        $RequestedTools = @()
    }

    $First = Invoke-JsonRequest -Method "POST" -Uri "$BaseUrl/api/agents/invoke" -Body @{
        adapter_name = $AdapterName
        payload = $Payload
        requested_capabilities = @($RequestedCapabilities)
        requested_tools = @($RequestedTools)
        mission_id = $MissionId
        step_id = $StepId
    }

    Show-JsonDebug -Value $First -Depth 40

    if ($First.status -eq "approval_required" -and $First.approval.approval_id) {
        $ApprovalId = $First.approval.approval_id

        Invoke-JsonRequest -Method "POST" -Uri "$BaseUrl/api/approvals/$ApprovalId/approve" -Body @{
            operator_note = $ApprovalNote
        } | Out-Null

        $Second = Invoke-JsonRequest -Method "POST" -Uri "$BaseUrl/api/agents/invoke" -Body @{
            adapter_name = $AdapterName
            approval_id = $ApprovalId
            payload = $Payload
            requested_capabilities = @($RequestedCapabilities)
            requested_tools = @($RequestedTools)
            mission_id = $MissionId
            step_id = $StepId
        }

        Show-JsonDebug -Value $Second -Depth 50
        return $Second
    }

    return $First
}

function Invoke-SopPhase2Finalize {
    param(
        [string]$ProjectRoot = "",
        [string]$Phase2WrapperPath = ""
    )

    $ErrorActionPreference = "Stop"

    if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
        if (Get-Command Resolve-ProjectRoot -ErrorAction SilentlyContinue) {
            $ProjectRoot = Resolve-ProjectRoot
        }
        else {
            $ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
        }
    }

    if ([string]::IsNullOrWhiteSpace($Phase2WrapperPath)) {
        $Phase2WrapperPath = Join-Path $ProjectRoot "scripts\jarvis_sop_phase2_finalize.ps1"
    }

    if (-not (Test-Path $Phase2WrapperPath)) {
        Write-Warning "Phase 2 finalize script not found: $Phase2WrapperPath"
        return $false
    }

    Write-Host ""
    Write-Host "== PHASE 2 FINALIZE ==" -ForegroundColor Cyan
    Write-Host ("Project root: {0}" -f $ProjectRoot) -ForegroundColor DarkGray
    Write-Host ("Script      : {0}" -f $Phase2WrapperPath) -ForegroundColor DarkGray

    & $Phase2WrapperPath -ProjectRoot $ProjectRoot

    if ($LASTEXITCODE -ne 0) {
        Write-Warning ("Phase 2 finalize returned exit code {0}" -f $LASTEXITCODE)
        return $false
    }

    Write-Host "Phase 2 finalize completed." -ForegroundColor Green
    return $true
}

function Assert-AdapterResponseApproved {
    param(
        [Parameter(Mandatory = $true)]$Response,
        [Parameter(Mandatory = $true)][string]$StepName,
        [string]$RunDir = ""
    )

    $status = ""
    if ($null -ne $Response -and $null -ne $Response.status) {
        $status = [string]$Response.status
    }

    $approvalStatus = ""
    if ($null -ne $Response -and $null -ne $Response.approval -and $null -ne $Response.approval.status) {
        $approvalStatus = [string]$Response.approval.status
    }

    $requiresApproval = $false
    if ($null -ne $Response -and $null -ne $Response.policy -and $null -ne $Response.policy.requires_approval) {
        $requiresApproval = [bool]$Response.policy.requires_approval
    }

    if ($status -eq "approval_required" -or $approvalStatus -eq "pending") {
        $payload = [ordered]@{
            ok                = $false
            step              = $StepName
            status            = $status
            approval_status   = $approvalStatus
            requires_approval = $requiresApproval
            message           = "Approval required. Pipeline stopped before unsafe continuation."
        }

        if (-not [string]::IsNullOrWhiteSpace($RunDir)) {
            try {
                New-Item -ItemType Directory -Path $RunDir -Force | Out-Null
                ($payload | ConvertTo-Json -Depth 20) | Set-Content -Path (Join-Path $RunDir ("approval_block_" + $StepName + ".json")) -Encoding utf8
            }
            catch {
                Write-Warning ("Failed to write approval block artifact: {0}" -f $_.Exception.Message)
            }
        }

        throw ("Approval required for step '{0}'. Approve or reject it first, then rerun." -f $StepName)
    }

    return $true
}

function Invoke-GovernedJsonRequest {
    [CmdletBinding()]
    param(
        [string]$Method,
        [string]$Uri,
        $Body = $null,
        $Headers = $null,
        [string]$ContentType = "",
        [string]$InFile = "",
        [string]$OutFile = "",
        [int]$TimeoutSec = 0,
        [string]$StepName = "api_step",
        [string]$RunDir = ""
    )

    $invokeArgs = @{}

    if ($PSBoundParameters.ContainsKey("Method"))      { $invokeArgs["Method"] = $Method }
    if ($PSBoundParameters.ContainsKey("Uri"))         { $invokeArgs["Uri"] = $Uri }
    if ($PSBoundParameters.ContainsKey("Body"))        { $invokeArgs["Body"] = $Body }
    if ($PSBoundParameters.ContainsKey("Headers"))     { $invokeArgs["Headers"] = $Headers }
    if ($PSBoundParameters.ContainsKey("ContentType") -and -not [string]::IsNullOrWhiteSpace($ContentType)) { $invokeArgs["ContentType"] = $ContentType }
    if ($PSBoundParameters.ContainsKey("InFile") -and -not [string]::IsNullOrWhiteSpace($InFile))           { $invokeArgs["InFile"] = $InFile }
    if ($PSBoundParameters.ContainsKey("OutFile") -and -not [string]::IsNullOrWhiteSpace($OutFile))         { $invokeArgs["OutFile"] = $OutFile }
    if ($PSBoundParameters.ContainsKey("TimeoutSec") -and $TimeoutSec -gt 0)                                 { $invokeArgs["TimeoutSec"] = $TimeoutSec }

    $response = Invoke-JsonRequest @invokeArgs

    if ($null -ne $response) {
        Assert-AdapterResponseApproved -Response $response -StepName $StepName -RunDir $RunDir
    }

    return $response
}

function Normalize-ArtifactRunDir {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string]$RunDir
    )

    if (-not (Test-Path $RunDir)) {
        Write-Warning "RunDir not found for normalization: $RunDir"
        return $false
    }

    $PyExeLocal = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path $PyExeLocal)) { $PyExeLocal = "python" }

    $NormalizeScript = Join-Path $ProjectRoot "scripts\jarvis_artifact_normalize_utf8.py"
    if (-not (Test-Path $NormalizeScript)) {
        Write-Warning "Normalize script not found: $NormalizeScript"
        return $false
    }

    & $PyExeLocal $NormalizeScript --run-dir $RunDir --recursive --pattern "*.md" --pattern "*.txt" --pattern "*.json" --pattern "*.log" --quiet

    if ($LASTEXITCODE -ne 0) {
        Write-Warning ("Artifact normalization returned exit code {0}" -f $LASTEXITCODE)
        return $false
    }

    return $true
}
