param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    [string]$BaseUrl = "http://127.0.0.1:8015",
    [int]$DurationHours = 6,
    [int]$SleepSeconds = 90,
    [int]$MaxIterations = 20
)

Set-ExecutionPolicy -Scope Process Bypass -Force
$ErrorActionPreference = "Continue"

Set-Location $ProjectRoot

$RunId = "nightv62_brain_safe_" + (Get-Date -Format "yyyyMMdd_HHmmss")
$RunRoot = Join-Path $ProjectRoot "jarvis_stage3_artifacts\night_v6_2_brain_safe\runs\$RunId"
New-Item -ItemType Directory -Path $RunRoot -Force | Out-Null

$LogPath = Join-Path $RunRoot "night_v6_2.log"
$StatePath = Join-Path $RunRoot "state.json"

function Log {
    param([string]$Message, [string]$Level = "INFO")
    $line = "[{0}] [{1}] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Level, $Message
    Write-Host $line
    Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
}

function Get-StrategicGoalTask {
    param([string]$FallbackTask)

    $TempPy = Join-Path $ProjectRoot "jarvis_stage3_artifacts\temp\night_goal_get.py"
    New-Item -ItemType Directory -Path (Split-Path $TempPy -Parent) -Force | Out-Null

    $Py = @"
from pathlib import Path
import sys
PROJECT_ROOT = Path(r'''$ProjectRoot''')
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from app.services.jarvis_autonomous_goal_generator import JarvisAutonomousGoalGenerator
print(JarvisAutonomousGoalGenerator(PROJECT_ROOT).next_goal_task(r'''$FallbackTask'''))
"@

    [System.IO.File]::WriteAllText($TempPy, $Py, [System.Text.UTF8Encoding]::new($false))
    $out = & $PyExe $TempPy
    if ($LASTEXITCODE -eq 0 -and $out) { return ($out -join "`n") }
    return $FallbackTask
}

function Generate-StrategicGoals {
    param([string]$Reason = "night_iteration")

    $TempPy = Join-Path $ProjectRoot "jarvis_stage3_artifacts\temp\night_goal_generate.py"
    New-Item -ItemType Directory -Path (Split-Path $TempPy -Parent) -Force | Out-Null

    $Py = @"
from pathlib import Path
import sys, json
PROJECT_ROOT = Path(r'''$ProjectRoot''')
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from app.services.jarvis_autonomous_goal_generator import JarvisAutonomousGoalGenerator
goals = JarvisAutonomousGoalGenerator(PROJECT_ROOT).generate_goals(count=5, reason=r'''$Reason''')
print(json.dumps([g.title for g in goals], ensure_ascii=False))
"@

    [System.IO.File]::WriteAllText($TempPy, $Py, [System.Text.UTF8Encoding]::new($false))
    $out = & $PyExe $TempPy
    if ($out) { Log "Generated strategic goals: $out" }
}

function SafeGet {
    param($Obj, [string[]]$Path, $Default = $null)
    $cur = $Obj
    foreach ($p in $Path) {
        if ($null -eq $cur) { return $Default }
        if ($cur.PSObject.Properties.Name -contains $p) {
            $cur = $cur.$p
        } else {
            return $Default
        }
    }
    if ($null -eq $cur) { return $Default }
    return $cur
}

function Invoke-JsonUtf8 {
    param([string]$Uri, [object]$Payload, [int]$TimeoutSec = 300)
    $Json = $Payload | ConvertTo-Json -Depth 30
    $Bytes = [System.Text.Encoding]::UTF8.GetBytes($Json)

    Invoke-RestMethod `
        -Method POST `
        -Uri $Uri `
        -ContentType "application/json; charset=utf-8" `
        -Body $Bytes `
        -TimeoutSec $TimeoutSec
}

$env:PYTHONPATH = $ProjectRoot

function Get-SeededTask {
    param([string]$FallbackTask)

    $TempPy = Join-Path $ProjectRoot "jarvis_stage3_artifacts\temp\night_task_seed_get.py"
    New-Item -ItemType Directory -Path (Split-Path $TempPy -Parent) -Force | Out-Null

    $Py = @"
from pathlib import Path
import sys
PROJECT_ROOT = Path(r'''$ProjectRoot''')
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from app.services.jarvis_autonomous_task_seeder import JarvisAutonomousTaskSeeder
print(JarvisAutonomousTaskSeeder(PROJECT_ROOT).next_task(r'''$FallbackTask'''))
"@
    [System.IO.File]::WriteAllText($TempPy, $Py, [System.Text.UTF8Encoding]::new($false))
    $out = & $PyExe $TempPy
    if ($LASTEXITCODE -eq 0 -and $out) { return ($out -join "`n") }
    return $FallbackTask
}

function Seed-NextTasks {
    param([string]$PreviousTask, [string]$Status, [string]$Lane)

    $TempPy = Join-Path $ProjectRoot "jarvis_stage3_artifacts\temp\night_task_seed_add.py"
    New-Item -ItemType Directory -Path (Split-Path $TempPy -Parent) -Force | Out-Null

    $Py = @"
from pathlib import Path
import sys, json
PROJECT_ROOT = Path(r'''$ProjectRoot''')
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from app.services.jarvis_autonomous_task_seeder import JarvisAutonomousTaskSeeder
new_tasks = JarvisAutonomousTaskSeeder(PROJECT_ROOT).seed_from_result(r'''$PreviousTask''', r'''$Status''', r'''$Lane''')
print(json.dumps(new_tasks, ensure_ascii=False))
"@
    [System.IO.File]::WriteAllText($TempPy, $Py, [System.Text.UTF8Encoding]::new($false))
    $out = & $PyExe $TempPy
    if ($out) { Log "Seeded next tasks: $out" }
}

function Invoke-ExecutionVerifier {
    param(
        [string]$TaskText,
        [string]$ResultPath,
        [int]$Attempt = 1
    )

    $TempPy = Join-Path $ProjectRoot "jarvis_stage3_artifacts\temp\execution_verify.py"
    New-Item -ItemType Directory -Path (Split-Path $TempPy -Parent) -Force | Out-Null

    $Py = @"
from pathlib import Path
import sys, json

PROJECT_ROOT = Path(r'''$ProjectRoot''')
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.jarvis_execution_verifier import JarvisExecutionVerifier

result_path = Path(r'''$ResultPath''')
result = json.loads(result_path.read_text(encoding='utf-8-sig'))
verifier = JarvisExecutionVerifier(PROJECT_ROOT)
report = verifier.verify(r'''$TaskText''', result, attempt=$Attempt, max_attempts=2)
print(json.dumps(report, ensure_ascii=False, default=str))
"@

    [System.IO.File]::WriteAllText($TempPy, $Py, [System.Text.UTF8Encoding]::new($false))
    $out = & $PyExe $TempPy
    if ($LASTEXITCODE -ne 0 -or -not $out) {
        return '{"verdict":"verifier_failed","next_action":"continue_safely"}'
    }
    return ($out -join "`n")
}

function Get-RetryTaskFromVerification {
    param(
        [string]$TaskText,
        [string]$VerificationJson
    )

    $TempPy = Join-Path $ProjectRoot "jarvis_stage3_artifacts\temp\execution_retry_task.py"
    New-Item -ItemType Directory -Path (Split-Path $TempPy -Parent) -Force | Out-Null

    $SafeJsonPath = Join-Path $ProjectRoot "jarvis_stage3_artifacts\temp\last_verification.json"
    [System.IO.File]::WriteAllText($SafeJsonPath, $VerificationJson, [System.Text.UTF8Encoding]::new($false))

    $Py = @"
from pathlib import Path
import sys, json

PROJECT_ROOT = Path(r'''$ProjectRoot''')
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.jarvis_execution_verifier import JarvisExecutionVerifier

verification = json.loads(Path(r'''$SafeJsonPath''').read_text(encoding='utf-8'))
print(JarvisExecutionVerifier(PROJECT_ROOT).retry_task_text(r'''$TaskText''', verification))
"@

    [System.IO.File]::WriteAllText($TempPy, $Py, [System.Text.UTF8Encoding]::new($false))
    $out = & $PyExe $TempPy
    if ($LASTEXITCODE -eq 0 -and $out) { return ($out -join "`n") }
    return $TaskText
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
    "Night self-fix: improve UTF-8, fix errors, improve night loop safety and save lessons",
    "Night improvement: improve Brain Executor execution lanes beyond n8n",
    "Night improvement: add memory learning loop for successful and failed executions",
    "Night improvement: add service connector registry for Telegram Google Sheets Gmail Calendar n8n HTTP API",
    "Night improvement: improve Task Compiler complexity detection provider routing and agent role selection",
    "Night improvement: improve n8n dynamic pipeline builder and prevent fallback Echo workflow",
    "Night improvement: create validation reports and rollback artifacts",
    "Night improvement: improve Telegram operator UX summaries and next actions"
)

$StartedAt = Get-Date
$Deadline = $StartedAt.AddHours($DurationHours)

$State = [ordered]@{
    run_id = $RunId
    started_at = $StartedAt.ToString("o")
    deadline = $Deadline.ToString("o")
    status = "running"
    iterations = @()
}

$State | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $StatePath -Encoding UTF8

Log "NIGHT MODE V6.2 SAFE BRAIN LOOP STARTED"
Log "RunId: $RunId"
Log "RunRoot: $RunRoot"

if (-not (Check-Health)) {
    Log "Backend unhealthy at start. Stop." "ERROR"
    exit 1
}

try {
    $brainHealth = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/brain-executor/health" -TimeoutSec 30
    Log "Brain Executor health OK: $($brainHealth.status)"
} catch {
    Log "Brain Executor health failed: $($_.Exception.Message)" "ERROR"
    exit 1
}

for ($i = 1; $i -le $MaxIterations; $i++) {
    if ((Get-Date) -ge $Deadline) {
        Log "Deadline reached. Stopping loop."
        break
    }

    $FallbackTask = $Tasks[($i - 1) % $Tasks.Count]; if (($i -eq 1) -or (($i % 4) -eq 1)) { Generate-StrategicGoals -Reason "night_iteration_$i" }; $Task = Get-StrategicGoalTask -FallbackTask (Get-SeededTask -FallbackTask $FallbackTask)
    $OutPath = Join-Path $RunRoot ("iteration_{0:D3}.json" -f $i)

    Log "---- ITERATION $i ----"
    Log "Task: $Task"

    try {
        $Payload = @{
            task = $Task
            dry_run = $false
        }

        $Result = Invoke-JsonUtf8 -Uri "$BaseUrl/api/brain-executor/run" -Payload $Payload -TimeoutSec 300
        $Result | ConvertTo-Json -Depth 60 | Set-Content -LiteralPath $OutPath -Encoding UTF8

        $VerificationJson = Invoke-ExecutionVerifier -TaskText $Task -ResultPath $OutPath -Attempt 1
        $Verification = $VerificationJson | ConvertFrom-Json
        Log "Verification verdict: $($Verification.verdict) next_action=$($Verification.next_action)"

        if ($Verification.verdict -eq "needs_retry") {
            $RetryTask = Get-RetryTaskFromVerification -TaskText $Task -VerificationJson $VerificationJson
            Log "Retrying with stricter evidence gate."
            $RetryPayload = @{
                task = $RetryTask
                dry_run = $false
            }
            $RetryResult = Invoke-JsonUtf8 -Uri "$BaseUrl/api/brain-executor/run" -Payload $RetryPayload -TimeoutSec 300
            $RetryOutPath = Join-Path $RunRoot ("iteration_{0:D3}_retry.json" -f $i)
            $RetryResult | ConvertTo-Json -Depth 60 | Set-Content -LiteralPath $RetryOutPath -Encoding UTF8

            $RetryVerificationJson = Invoke-ExecutionVerifier -TaskText $RetryTask -ResultPath $RetryOutPath -Attempt 2
            $RetryVerification = $RetryVerificationJson | ConvertFrom-Json
            Log "Retry verification verdict: $($RetryVerification.verdict) next_action=$($RetryVerification.next_action)"

            if ($RetryVerification.verdict -eq "verified_completed") {
                $Result = $RetryResult
                $OutPath = $RetryOutPath
            }
        }

        $Status = SafeGet $Result @("execution","status") "unknown"
        $Lane = SafeGet $Result @("execution","primary_result","lane") "none"
        $WorkflowId = SafeGet $Result @("execution","primary_result","workflow_id") "none"
        $WorkflowStatus = SafeGet $Result @("execution","primary_result","status") "none"
        $CodeRunId = SafeGet $Result @("execution","primary_result","run_id") "none"

        Log "Iteration $i completed: status=$Status lane=$Lane workflow=$WorkflowId workflow_status=$WorkflowStatus code_run=$CodeRunId"; Seed-NextTasks -PreviousTask $Task -Status $Status -Lane $Lane

        $State.iterations += @{
            iteration = $i
            task = $Task
            status = $Status
            lane = $Lane
            workflow_id = $WorkflowId
            workflow_status = $WorkflowStatus
            code_run_id = $CodeRunId
            artifact = $OutPath
            finished_at = (Get-Date).ToString("o")
        }

    } catch {
        Log "Iteration $i failed safely: $($_.Exception.Message)" "ERROR"

        $State.iterations += @{
            iteration = $i
            task = $Task
            status = "failed"
            error = $_.Exception.Message
            finished_at = (Get-Date).ToString("o")
        }
    }

    $State | ConvertTo-Json -Depth 40 | Set-Content -LiteralPath $StatePath -Encoding UTF8

    Check-Health | Out-Null

    if ($i -lt $MaxIterations) {
        Log "Sleeping $SleepSeconds seconds before next iteration."
        Start-Sleep -Seconds $SleepSeconds
    }
}

$State.status = "completed"
$State.finished_at = (Get-Date).ToString("o")
$State | ConvertTo-Json -Depth 40 | Set-Content -LiteralPath $StatePath -Encoding UTF8

Log "NIGHT MODE V6.2 SAFE BRAIN LOOP FINISHED"
Log "State: $StatePath"