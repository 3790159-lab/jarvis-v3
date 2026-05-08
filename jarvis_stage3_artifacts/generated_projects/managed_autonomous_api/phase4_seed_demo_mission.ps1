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

function Read-JsonFile {
    param([string]$Path)

    if (-not (Test-Path $Path)) {
        return $null
    }

    return (Get-Content -Path $Path -Raw -Encoding UTF8 | ConvertFrom-Json)
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
$ArtifactsDir = Join-Path $ProjectRoot "artifacts"

$MissionsFile = Join-Path $StateDir "missions.json"
$TasksFile = Join-Path $StateDir "tasks.json"
$SeedReportFile = Join-Path $ArtifactsDir "phase4_seed_demo_mission.json"

$missionsData = Read-JsonFile -Path $MissionsFile
$tasksData = Read-JsonFile -Path $TasksFile

if ($null -eq $missionsData) {
    throw "missions.json not found or invalid"
}

if ($null -eq $tasksData) {
    throw "tasks.json not found or invalid"
}

$missionId = "mission_demo_{0}" -f (Get-Date -Format "yyyyMMdd_HHmmss")
$goalId = "goal_demo_{0}" -f (Get-Date -Format "yyyyMMdd_HHmmss")
$now = (Get-Date).ToString("s")

$demoMission = [ordered]@{
    mission_id = $missionId
    goal_id = $goalId
    objective = "Validate mission contract layer and task orchestration foundation"
    status = "drafted"
    created_at = $now
    updated_at = $now
    constraints = [ordered]@{
        mode = "safe"
        require_structured_result = $true
    }
    summary = "Seeded demo mission for phase 4 contract verification"
    tasks = @(
        "task_demo_validate_contracts",
        "task_demo_generate_summary"
    )
    metadata = [ordered]@{
        source = "phase4_seed_demo_mission.ps1"
    }
}

$demoTasks = @(
    [ordered]@{
        task_id = "task_demo_validate_contracts"
        mission_id = $missionId
        title = "Validate mission and task contracts"
        type = "contract_validation"
        status = "queued"
        created_at = $now
        updated_at = $now
        agent = "critic_agent"
        attempt_count = 0
        max_attempts = 2
        depends_on = @()
        input = [ordered]@{
            target = "contracts"
        }
        metadata = [ordered]@{
            source = "phase4_seed_demo_mission.ps1"
        }
    },
    [ordered]@{
        task_id = "task_demo_generate_summary"
        mission_id = $missionId
        title = "Generate mission summary"
        type = "summary_generation"
        status = "queued"
        created_at = $now
        updated_at = $now
        agent = "planner_agent"
        attempt_count = 0
        max_attempts = 2
        depends_on = @("task_demo_validate_contracts")
        input = [ordered]@{
            target = "mission_summary"
        }
        metadata = [ordered]@{
            source = "phase4_seed_demo_mission.ps1"
        }
    }
)

$missionsData.items += $demoMission
foreach ($task in $demoTasks) {
    $tasksData.items += $task
}

Write-JsonAtomic -Path $MissionsFile -Object $missionsData
Write-JsonAtomic -Path $TasksFile -Object $tasksData

$report = [ordered]@{
    created_at = $now
    mission_id = $missionId
    goal_id = $goalId
    task_ids = @($demoTasks | ForEach-Object { $_.task_id })
    status = "ok"
}

Write-JsonAtomic -Path $SeedReportFile -Object $report
Write-Host "Phase 4 demo mission seeded."
$report | ConvertTo-Json -Depth 10
