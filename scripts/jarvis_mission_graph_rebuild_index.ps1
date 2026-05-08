param(
    [string]$ProjectRoot = (Get-Location).Path
)

$ErrorActionPreference = "Stop"

$missionsRoot = Join-Path $ProjectRoot "artifacts\mission_graph\missions"
$indexesRoot = Join-Path $ProjectRoot "artifacts\mission_graph\indexes"
$indexPath = Join-Path $indexesRoot "latest_graph_runs.json"

if (-not (Test-Path $missionsRoot)) {
    throw "Mission graph missions folder not found: $missionsRoot"
}

New-Item -ItemType Directory -Force -Path $indexesRoot | Out-Null

$items = @()

Get-ChildItem $missionsRoot -Filter *.json | ForEach-Object {
    $raw = Get-Content $_.FullName -Raw -Encoding UTF8
    $obj = $raw | ConvertFrom-Json

    $steps = @($obj.steps)
    $entry = [ordered]@{
        graph_mission_id = $obj.graph_mission_id
        status           = $obj.status
        execution_state  = $obj.execution_state
        graph_kind       = $obj.graph_kind
        objective        = $obj.objective
        auto_execute     = $obj.auto_execute
        can_execute      = $obj.can_execute
        can_cancel       = $obj.can_cancel
        created_at       = $obj.created_at
        updated_at       = $obj.updated_at
        steps_total      = $steps.Count
        steps_completed  = @($steps | Where-Object { $_.status -eq "completed" }).Count
        steps_failed     = @($steps | Where-Object { $_.status -eq "failed" }).Count
        steps_skipped    = @($steps | Where-Object { $_.status -eq "skipped" }).Count
        steps_ready      = @($steps | Where-Object { $_.status -eq "ready" }).Count
        steps_pending    = @($steps | Where-Object { $_.status -eq "pending" }).Count
        steps_running    = @($steps | Where-Object { $_.status -eq "running" }).Count
    }

    $items += [pscustomobject]$entry
}

$sorted = $items | Sort-Object updated_at -Descending
$sorted | ConvertTo-Json -Depth 20 | Set-Content -Path $indexPath -Encoding UTF8

Write-Host "Rebuilt mission graph index: $indexPath"
Write-Host "Entries: $($sorted.Count)"
