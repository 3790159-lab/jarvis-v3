[CmdletBinding()]
param(
    [string]$ProjectRoot = (Get-Location).Path
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-AbsolutePath {
    param([string]$PathValue)

    if ([string]::IsNullOrWhiteSpace($PathValue)) {
        return (Get-Location).Path
    }

    try {
        return [System.IO.Path]::GetFullPath((Resolve-Path -Path $PathValue).Path)
    }
    catch {
        return [System.IO.Path]::GetFullPath($PathValue)
    }
}

function Ensure-Dir {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
}

function Write-JsonAtomic {
    param(
        [string]$Path,
        [object]$Object
    )

    $tmp = "{0}.{1}.tmp" -f $Path, ([guid]::NewGuid().ToString("N"))
    $Object | ConvertTo-Json -Depth 30 | Set-Content -Path $tmp -Encoding UTF8
    Move-Item -Path $tmp -Destination $Path -Force
}

$ProjectRoot = Resolve-AbsolutePath -PathValue $ProjectRoot

$StateDir = Join-Path $ProjectRoot "state"
$ContractsDir = Join-Path $ProjectRoot "contracts"
$ArtifactsDir = Join-Path $ProjectRoot "artifacts"

Ensure-Dir -Path $StateDir
Ensure-Dir -Path $ContractsDir
Ensure-Dir -Path $ArtifactsDir

$MissionContractFile = Join-Path $ContractsDir "mission_contract.json"
$TaskContractFile = Join-Path $ContractsDir "task_contract.json"
$StatusRulesFile = Join-Path $ContractsDir "status_transition_rules.json"
$Phase4SetupReport = Join-Path $ArtifactsDir "phase4_contract_setup_report.json"

$missionContract = [ordered]@{
    schema_version = 1
    entity = "mission"
    required_fields = @(
        "mission_id",
        "goal_id",
        "objective",
        "status",
        "created_at",
        "updated_at",
        "tasks"
    )
    optional_fields = @(
        "constraints",
        "summary",
        "result",
        "error",
        "metadata"
    )
    allowed_statuses = @(
        "drafted",
        "queued",
        "running",
        "waiting_input",
        "completed",
        "failed",
        "aborted"
    )
}

$taskContract = [ordered]@{
    schema_version = 1
    entity = "task"
    required_fields = @(
        "task_id",
        "mission_id",
        "title",
        "type",
        "status",
        "created_at",
        "updated_at"
    )
    optional_fields = @(
        "agent",
        "input",
        "result",
        "error",
        "attempt_count",
        "max_attempts",
        "depends_on",
        "metadata"
    )
    allowed_statuses = @(
        "queued",
        "running",
        "completed",
        "failed",
        "skipped",
        "waiting_input",
        "cancelled"
    )
}

$statusRules = [ordered]@{
    schema_version = 1
    mission_transitions = [ordered]@{
        drafted       = @("queued", "aborted")
        queued        = @("running", "aborted")
        running       = @("waiting_input", "completed", "failed", "aborted")
        waiting_input = @("running", "aborted")
        completed     = @()
        failed        = @("queued", "aborted")
        aborted       = @()
    }
    task_transitions = [ordered]@{
        queued        = @("running", "cancelled", "skipped")
        running       = @("completed", "failed", "waiting_input", "cancelled")
        waiting_input = @("running", "cancelled")
        completed     = @()
        failed        = @("queued", "cancelled")
        skipped       = @()
        cancelled     = @()
    }
}

Write-JsonAtomic -Path $MissionContractFile -Object $missionContract
Write-JsonAtomic -Path $TaskContractFile -Object $taskContract
Write-JsonAtomic -Path $StatusRulesFile -Object $statusRules

$report = [ordered]@{
    created_at = (Get-Date).ToString("s")
    project_root = $ProjectRoot
    files = [ordered]@{
        mission_contract = $MissionContractFile
        task_contract = $TaskContractFile
        status_rules = $StatusRulesFile
    }
    status = "ok"
}

Write-JsonAtomic -Path $Phase4SetupReport -Object $report
Write-Host "Phase 4 contract setup complete."
$report | ConvertTo-Json -Depth 10
