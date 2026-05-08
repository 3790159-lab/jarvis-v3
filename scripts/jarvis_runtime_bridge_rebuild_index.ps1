param(
    [string]$ProjectRoot = (Get-Location).Path
)

$ErrorActionPreference = "Stop"

$missionsRoot = Join-Path $ProjectRoot "artifacts\runtime_bridge\missions"
$indexesRoot = Join-Path $ProjectRoot "artifacts\runtime_bridge\indexes"
$indexPath = Join-Path $indexesRoot "latest_runs.json"

if (-not (Test-Path $missionsRoot)) {
    throw "Runtime bridge missions folder not found: $missionsRoot"
}

New-Item -ItemType Directory -Force -Path $indexesRoot | Out-Null

$items = @()

Get-ChildItem $missionsRoot -Filter *.json | ForEach-Object {
    $raw = Get-Content $_.FullName -Raw -Encoding UTF8
    $obj = $raw | ConvertFrom-Json

    $execution = $obj.execution
    $artifactResult = $null
    if ($execution) {
        $artifactResult = $execution.artifact_result
    }

    $entry = [ordered]@{
        goal_id          = $obj.goal_id
        mission_id       = $obj.mission_id
        status           = $obj.status
        mode             = $obj.mode
        objective        = $obj.objective
        selected_task_type = $obj.selected_task_type
        artifact_name    = $obj.artifact_name
        planning_state   = $obj.planning_state
        execution_state  = $obj.execution_state
        can_execute      = $obj.can_execute
        can_cancel       = $obj.can_cancel
        created_at       = $obj.created_at
        updated_at       = $obj.updated_at
        run_id           = if ($execution) { $execution.run_id } else { $null }
        artifact_task_id = if ($artifactResult) { $artifactResult.task_id } else { $null }
    }

    $items += [pscustomobject]$entry
}

$sorted = $items | Sort-Object updated_at -Descending
$sorted | ConvertTo-Json -Depth 20 | Set-Content -Path $indexPath -Encoding UTF8

Write-Host "Rebuilt runtime bridge index: $indexPath"
Write-Host "Entries: $($sorted.Count)"
