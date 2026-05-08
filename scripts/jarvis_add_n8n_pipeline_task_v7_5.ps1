param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram"
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectRoot

$QueuePath = Join-Path $ProjectRoot "state\jarvis_brain\action_queue_v6_4.json"
if (-not (Test-Path $QueuePath)) {
    throw "Queue missing: $QueuePath"
}

$Q = Get-Content $QueuePath -Raw -Encoding UTF8 | ConvertFrom-Json
$TaskId = "auto_n8n_pipeline_creator_v7_5"

$Exists = $false
foreach ($Item in $Q.items) {
    if ($Item.id -eq $TaskId) { $Exists = $true }
}

if (-not $Exists) {
    $NewTask = [ordered]@{
        id = $TaskId
        title = "Auto create n8n pipeline templates"
        details = "Generate importable n8n workflow JSON files for Jarvis pipeline integration."
        priority = 18
        risk = "low"
        lane = "n8n"
        status = "pending"
        created_at = (Get-Date).ToString("s")
        executor = "gateway_plan_v7_2"
        evidence_required = $true
        gateway_plan = @(
            @{
                tool = "n8n_workflow_stub"
                args = @{
                    name = "Jarvis Pipeline Creator Stub"
                    path = "jarvis_stage3_artifacts\n8n_workflows_v7_5\pipeline_creator_stub.json"
                }
            },
            @{
                tool = "md_report"
                args = @{
                    path = "jarvis_stage3_artifacts\auto_reports_v7_5\n8n_pipeline_creator_task.md"
                    title = "n8n Pipeline Creator Task"
                    body = "V7.5 task queued. Dedicated creator script should also be executed."
                }
            }
        )
    }

    $Q.items += $NewTask
    $Q.updated_at = (Get-Date).ToString("s")
    $Q | ConvertTo-Json -Depth 30 | Set-Content -Path $QueuePath -Encoding UTF8
    Write-Host "Added task: $TaskId" -ForegroundColor Green
} else {
    Write-Host "Task already exists: $TaskId" -ForegroundColor Yellow
}