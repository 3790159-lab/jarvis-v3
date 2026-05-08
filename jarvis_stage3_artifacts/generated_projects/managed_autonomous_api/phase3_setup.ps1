[CmdletBinding()]
param(
    [string]$ProjectRoot = (Get-Location).Path,
    [int]$Port = 8010
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

$ProjectRoot = Resolve-AbsolutePath -PathValue $ProjectRoot

$StateDir      = Join-Path $ProjectRoot "state"
$BackupsDir    = Join-Path $StateDir "backups"
$ArtifactsDir  = Join-Path $ProjectRoot "artifacts"
$LogsDir       = Join-Path $ArtifactsDir "logs"
$PoliciesDir   = Join-Path $ProjectRoot "policies"

$MissionsFile  = Join-Path $StateDir "missions.json"
$TasksFile     = Join-Path $StateDir "tasks.json"
$AgentsFile    = Join-Path $StateDir "agents.json"
$QueueFile     = Join-Path $StateDir "queue.json"
$RuntimeConfig = Join-Path $StateDir "runtime_config.json"
$PolicyFile    = Join-Path $PoliciesDir "supervisor_policy.json"
$Phase3Report  = Join-Path $ArtifactsDir "phase3_setup_report.json"

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
    $Object | ConvertTo-Json -Depth 20 | Set-Content -Path $tmp -Encoding UTF8
    Move-Item -Path $tmp -Destination $Path -Force
}

function Backup-FileIfExists {
    param([string]$Path)

    if (-not (Test-Path $Path)) {
        return $null
    }

    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $name = [System.IO.Path]::GetFileName($Path)
    $dest = Join-Path $BackupsDir ("{0}.{1}.bak" -f $name, $timestamp)
    Copy-Item -Path $Path -Destination $dest -Force
    return $dest
}

function Ensure-JsonFile {
    param(
        [string]$Path,
        [object]$DefaultObject
    )

    if (-not (Test-Path $Path)) {
        Write-JsonAtomic -Path $Path -Object $DefaultObject
        return "created"
    }

    try {
        $raw = Get-Content -Path $Path -Raw -Encoding UTF8
        if ([string]::IsNullOrWhiteSpace($raw)) {
            Write-JsonAtomic -Path $Path -Object $DefaultObject
            return "reinitialized_empty"
        }

        $null = $raw | ConvertFrom-Json
        return "ok"
    }
    catch {
        Backup-FileIfExists -Path $Path | Out-Null
        Write-JsonAtomic -Path $Path -Object $DefaultObject
        return "reinitialized_invalid"
    }
}

Ensure-Dir -Path $StateDir
Ensure-Dir -Path $BackupsDir
Ensure-Dir -Path $ArtifactsDir
Ensure-Dir -Path $LogsDir
Ensure-Dir -Path $PoliciesDir

$missionsDefault = [ordered]@{
    schema_version = 1
    items = @()
}

$tasksDefault = [ordered]@{
    schema_version = 1
    items = @()
}

$agentsDefault = [ordered]@{
    schema_version = 1
    items = @()
}

$queueDefault = [ordered]@{
    schema_version = 1
    queued = @()
    running = @()
    dead_letter = @()
}

$runtimeDefault = [ordered]@{
    schema_version = 1
    service_name = "managed_autonomous_api"
    port = $Port
    mission_defaults = [ordered]@{
        max_retries_per_task = 2
        default_task_timeout_seconds = 300
        auto_requeue_orphans = $true
    }
    health_policy = [ordered]@{
        consecutive_failures_before_degraded = 3
        consecutive_failures_before_error = 5
        health_check_interval_seconds = 5
    }
    logging_policy = [ordered]@{
        keep_last_snapshot_count = 20
        keep_last_log_count = 20
    }
}

$policyDefault = [ordered]@{
    schema_version = 1
    execution_mode = "safe"
    allow_shell_agent = $true
    shell_guardrails = [ordered]@{
        deny_patterns = @(
            "Remove-Item\s+-Recurse\s+-Force\s+C:\\",
            "format\s+[a-zA-Z]:",
            "shutdown",
            "reg delete HKLM"
        )
        max_output_chars = 20000
        timeout_seconds = 600
    }
    mission_policy = [ordered]@{
        require_structured_result = $true
        require_status_transition_validation = $true
    }
}

$backups = @()
foreach ($path in @($MissionsFile, $TasksFile, $AgentsFile, $QueueFile, $RuntimeConfig, $PolicyFile)) {
    $backup = Backup-FileIfExists -Path $path
    if ($null -ne $backup) {
        $backups += $backup
    }
}

$missionsStatus = Ensure-JsonFile -Path $MissionsFile -DefaultObject $missionsDefault
$tasksStatus    = Ensure-JsonFile -Path $TasksFile -DefaultObject $tasksDefault
$agentsStatus   = Ensure-JsonFile -Path $AgentsFile -DefaultObject $agentsDefault
$queueStatus    = Ensure-JsonFile -Path $QueueFile -DefaultObject $queueDefault
$runtimeStatus  = Ensure-JsonFile -Path $RuntimeConfig -DefaultObject $runtimeDefault
$policyStatus   = Ensure-JsonFile -Path $PolicyFile -DefaultObject $policyDefault

$report = [ordered]@{
    created_at = (Get-Date).ToString("s")
    project_root = $ProjectRoot
    files = [ordered]@{
        missions = $missionsStatus
        tasks = $tasksStatus
        agents = $agentsStatus
        queue = $queueStatus
        runtime_config = $runtimeStatus
        supervisor_policy = $policyStatus
    }
    backups = @($backups)
    status = "ok"
}

Write-JsonAtomic -Path $Phase3Report -Object $report
Write-Host "Phase 3 setup complete."
$report | ConvertTo-Json -Depth 10
