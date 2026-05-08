param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    [string]$BaseUrl = "http://127.0.0.1:8015",
    [int]$DurationHours = 6,
    [int]$SleepSeconds = 90,
    [int]$MaxIterations = 20
)

Set-ExecutionPolicy -Scope Process Bypass -Force
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Set-Location $ProjectRoot

$RunId = "nightv6_brain_" + (Get-Date -Format "yyyyMMdd_HHmmss")
$RunRoot = Join-Path $ProjectRoot "jarvis_stage3_artifacts\night_v6_brain\runs\$RunId"
New-Item -ItemType Directory -Path $RunRoot -Force | Out-Null

$LogPath = Join-Path $RunRoot "night_v6_brain.log"
$StatePath = Join-Path $RunRoot "state.json"

function Log {
    param([string]$Message, [string]$Level = "INFO")
    $line = "[{0}] [{1}] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Level, $Message
    Write-Host $line
    Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
}

function Invoke-JsonUtf8 {
    param(
        [string]$Uri,
        [object]$Payload,
        [int]$TimeoutSec = 240
    )

    $Json = $Payload | ConvertTo-Json -Depth 30
    $Bytes = [System.Text.Encoding]::UTF8.GetBytes($Json)

    Invoke-RestMethod `
        -Method POST `
        -Uri $Uri `
        -ContentType "application/json; charset=utf-8" `
        -Body $Bytes `
        -TimeoutSec $TimeoutSec
}

function Check-Health {
    try {
        $h = Invoke-RestMethod -Method GET -Uri "$BaseUrl/health" -TimeoutSec 20
        Log "Backend health OK: $($h.status)"
        return $true
    } catch {
        Log "Backend health failed: $($_.Exception.Message)" "ERROR"
        return $false
    }
}

$Tasks = @(
    "Night Mode V6 iteration: inspect Jarvis system and improve UTF-8 report stability. Keep changes safe, validated, reversible.",
    "Night Mode V6 iteration: improve Brain Executor execution lanes and make it capable of more task types beyond n8n. Keep low-risk.",
    "Night Mode V6 iteration: improve memory learning loop. Save lessons from successes, failures, validation results and rollback decisions.",
    "Night Mode V6 iteration: improve service connector registry for Telegram, Google Sheets, Gmail, Calendar, n8n and HTTP API. Do not expose secrets.",
    "Night Mode V6 iteration: improve Task Compiler complexity detection, provider routing and agent role selection.",
    "Night Mode V6 iteration: improve n8n dynamic pipeline builder so dynamic_pipeline always creates full chain, not fallback Echo workflow.",
    "Night Mode V6 iteration: create validation report and rollback artifact for every automated improvement.",
    "Night Mode V6 iteration: improve Telegram operator UX: clearer summaries, less duplicated text, better next actions."
)

$StartedAt = Get-Date
$Deadline = $StartedAt.AddHours($DurationHours)

$State = @{
    run_id = $RunId
    started_at = $StartedAt.ToString("o")
    deadline = $Deadline.ToString("o")
    iterations = @()
    status = "running"
    base_url = $BaseUrl
    mode = "brain_autonomous_loop"
}

$State | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $StatePath -Encoding UTF8

Log "NIGHT MODE V6 BRAIN LOOP STARTED"
Log "RunId: $RunId"
Log "RunRoot: $RunRoot"
Log "DurationHours: $DurationHours"
Log "MaxIterations: $MaxIterations"

if (-not (Check-Health)) {
    throw "Backend is not healthy. Start backend first."
}

try {
    $brainHealth = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/brain-executor/health" -TimeoutSec 30
    Log "Brain Executor health OK: $($brainHealth.status)"
} catch {
    Log "Brain Executor health failed: $($_.Exception.Message)" "ERROR"
    throw
}

for ($i = 1; $i -le $MaxIterations; $i++) {
    if ((Get-Date) -ge $Deadline) {
        Log "Deadline reached. Stopping loop."
        break
    }

    $Task = $Tasks[($i - 1) % $Tasks.Count]

    Log "---- ITERATION $i ----"
    Log "Task: $Task"

    $Payload = @{
        task = $Task
        dry_run = $false
    }

    $OutPath = Join-Path $RunRoot ("iteration_{0:D3}.json" -f $i)

    try {
        $Result = Invoke-JsonUtf8 -Uri "$BaseUrl/api/brain-executor/run" -Payload $Payload -TimeoutSec 300

        $Result | ConvertTo-Json -Depth 50 | Set-Content -LiteralPath $OutPath -Encoding UTF8

        $Status = "unknown"
        $WorkflowId = $null
        $WorkflowStatus = $null

        if ($null -ne $Result -and $null -ne $Result.execution) {
            $Status = $Result.execution.status

            if ($null -ne $Result.execution.primary_result) {
                $WorkflowId = if ($Result.execution.primary_result) { $Result.execution.primary_result.workflow_id } else { "none" }
                $WorkflowStatus = if ($Result.execution.primary_result) { $Result.execution.primary_result.status } else { "none" }
            }
        }

        if (-not $WorkflowId) { $WorkflowId = "none" }
        if (-not $WorkflowStatus) { $WorkflowStatus = "none" }

        Log "Iteration $i completed: status=$Status workflow=$WorkflowId workflow_status=$WorkflowStatus"

        $State.iterations += @{
            iteration = $i
            task = $Task
            status = $Status
            workflow_id = $WorkflowId
            workflow_status = $WorkflowStatus
            artifact = $OutPath
            finished_at = (Get-Date).ToString("o")
        }

    } catch {
        Log "Iteration $i failed: $($_.Exception.Message)" "ERROR"

        $State.iterations += @{
            iteration = $i
            task = $Task
            status = "failed"
            error = $_.Exception.Message
            finished_at = (Get-Date).ToString("o")
        }
    }

    $State | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $StatePath -Encoding UTF8

    if (-not (Check-Health)) {
        Log "Backend became unhealthy. Stopping for safety." "ERROR"
        break
    }

    if ($i -lt $MaxIterations) {
        Log "Sleeping $SleepSeconds seconds before next iteration."
        Start-Sleep -Seconds $SleepSeconds
    }
}

$State.status = "completed"
$State.finished_at = (Get-Date).ToString("o")
$State | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $StatePath -Encoding UTF8

Log "NIGHT MODE V6 BRAIN LOOP FINISHED"
Log "State: $StatePath"
Log "Artifacts: $RunRoot"