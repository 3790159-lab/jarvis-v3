param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",

    # How long this loop should run in this console session.
    # Use 8-10 hours for night mode, or 24 for full-day.
    [int]$DurationHours = 8,

    # Main tick interval.
    [int]$TickSeconds = 300,

    # Safe Gateway limit per tick.
    [int]$GatewayLimit = 5,

    # Night window in local Kyiv time.
    [int]$NightStartHour = 2,
    [int]$NightEndHour = 6,

    # Run Night Mode once per night window.
    [switch]$EnableNightMode,

    # Do not actually run gateway/night mode, only log decisions.
    [switch]$DryRun
)

Set-ExecutionPolicy -Scope Process Bypass -Force
Set-Location $ProjectRoot

$PyExe = ".\.venv\Scripts\python.exe"
$env:PYTHONPATH = $ProjectRoot

$RunId = "autoloop_24_7_" + (Get-Date -Format "yyyyMMdd_HHmmss")
$RunDir = "jarvis_stage3_artifacts\autoloop_24_7\runs\$RunId"
$LockPath = "state\jarvis_brain\autoloop_24_7.lock"
$StopPath = "state\jarvis_brain\autoloop_24_7.stop"
$LogPath = Join-Path $RunDir "autoloop.log"
$StatePath = Join-Path $RunDir "autoloop_state.json"

New-Item -ItemType Directory -Force -Path $RunDir | Out-Null

function Log-Line {
    param([string]$Message, [string]$Color = "Gray")
    $Line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Write-Host $Line -ForegroundColor $Color
    Add-Content -Path $LogPath -Value $Line -Encoding UTF8
}

function Save-State {
    param([hashtable]$State)
    $State | ConvertTo-Json -Depth 20 | Set-Content -Path $StatePath -Encoding UTF8
}

function Test-BackendHealth {
    try {
        $Health = Invoke-RestMethod -Method GET -Uri "http://127.0.0.1:8015/health" -TimeoutSec 8
        return @{
            ok = $true
            payload = $Health
            error = $null
        }
    } catch {
        return @{
            ok = $false
            payload = $null
            error = $_.Exception.Message
        }
    }
}

function Ensure-Backend {
    $Health = Test-BackendHealth
    if ($Health.ok) {
        Log-Line "Backend healthy." "Green"
        return $true
    }

    Log-Line "Backend unhealthy: $($Health.error)" "Yellow"

    if ($DryRun) {
        Log-Line "DRY RUN: would restart backend." "Yellow"
        return $false
    }

    if (Test-Path ".\scripts\jarvis_backend_restart_hardened.ps1") {
        Log-Line "Restarting backend with hardened script..." "Yellow"
        & ".\scripts\jarvis_backend_restart_hardened.ps1"

        $Health2 = Test-BackendHealth
        if ($Health2.ok) {
            Log-Line "Backend recovered after restart." "Green"
            return $true
        }

        Log-Line "Backend still unhealthy after restart: $($Health2.error)" "Red"
        return $false
    }

    Log-Line "Restart script not found." "Red"
    return $false
}

function Get-TimeContext {
    try {
        return Invoke-RestMethod -Method GET -Uri "http://127.0.0.1:8015/api/time-brain/now" -TimeoutSec 8
    } catch {
        return $null
    }
}

function Invoke-TimeTick {
    Log-Line "Running Time Brain tick..." "Cyan"

    if ($DryRun) {
        Log-Line "DRY RUN: would run jarvis_time_brain_tick_v1.ps1." "Yellow"
        return
    }

    if (Test-Path ".\scripts\jarvis_time_brain_tick_v1.ps1") {
        & ".\scripts\jarvis_time_brain_tick_v1.ps1" -ProjectRoot $ProjectRoot -Limit $GatewayLimit
    } else {
        Log-Line "Time Brain tick script not found." "Red"
    }
}

function Invoke-SafeGateway {
    Log-Line "Running Safe Gateway executor..." "Cyan"

    if ($DryRun) {
        Log-Line "DRY RUN: would run jarvis_safe_gateway_plan_executor_v1_3.ps1." "Yellow"
        return
    }

    if (Test-Path ".\scripts\jarvis_safe_gateway_plan_executor_v1_3.ps1") {
        & ".\scripts\jarvis_safe_gateway_plan_executor_v1_3.ps1" -Limit $GatewayLimit
    } else {
        Log-Line "Safe Gateway script not found." "Red"
    }
}

function Invoke-NightPreflight {
    Log-Line "Running Night Mode preflight..." "Magenta"

    try {
        $Body = @{
            source = "autoloop_24_7_v1"
            run_id = $RunId
            policy = "safe_package_first"
        } | ConvertTo-Json -Depth 10

        Invoke-RestMethod `
            -Method POST `
            -Uri "http://127.0.0.1:8015/api/claude-ecosystem/safe-night/preflight" `
            -ContentType "application/json" `
            -Body $Body `
            -TimeoutSec 20 | ConvertTo-Json -Depth 30 | Out-File -FilePath (Join-Path $RunDir "night_preflight.json") -Encoding UTF8

        Log-Line "Night preflight OK." "Green"
    } catch {
        Log-Line "Night preflight failed: $($_.Exception.Message)" "Red"
    }
}

function Invoke-NightModeOnce {
    Log-Line "Night Mode window detected." "Magenta"

    Invoke-NightPreflight

    if ($DryRun) {
        Log-Line "DRY RUN: would run night mode script if configured." "Yellow"
        return
    }

    # Prefer your unified/night scripts if present.
    $NightScripts = @(
        ".\scripts\jarvis_unified_night_run.ps1",
        ".\scripts\jarvis_night_mode_v5_1_code_gate_evolution_executor.ps1",
        ".\scripts\jarvis_night_mode_v5_1_3_safe_runtime_fixed.ps1"
    )

    $ScriptToRun = $null
    foreach ($S in $NightScripts) {
        if (Test-Path $S) {
            $ScriptToRun = $S
            break
        }
    }

    if ($null -eq $ScriptToRun) {
        Log-Line "No known Night Mode script found. Running Safe Gateway only." "Yellow"
        Invoke-SafeGateway
        return
    }

    Log-Line "Running Night Mode script: $ScriptToRun" "Magenta"

    try {
        & $ScriptToRun -DurationHours 3
        Log-Line "Night Mode script finished." "Green"
    } catch {
        Log-Line "Night Mode script failed: $($_.Exception.Message)" "Red"
    }

    try {
        $Body = @{
            source = "autoloop_24_7_v1"
            run_id = $RunId
            result = "night_mode_finished"
        } | ConvertTo-Json -Depth 10

        Invoke-RestMethod `
            -Method POST `
            -Uri "http://127.0.0.1:8015/api/claude-ecosystem/safe-night/postflight" `
            -ContentType "application/json" `
            -Body $Body `
            -TimeoutSec 20 | ConvertTo-Json -Depth 30 | Out-File -FilePath (Join-Path $RunDir "night_postflight.json") -Encoding UTF8
    } catch {
        Log-Line "Night postflight failed: $($_.Exception.Message)" "Yellow"
    }
}

# Prevent duplicate loop
if (Test-Path $LockPath) {
    $Existing = Get-Content $LockPath -Raw -ErrorAction SilentlyContinue
    Log-Line "Existing lock found: $Existing" "Yellow"
    Log-Line "Remove $LockPath only if you are sure no AutoLoop is running." "Yellow"
    throw "AutoLoop lock exists."
}

Remove-Item $StopPath -Force -ErrorAction SilentlyContinue
Set-Content -Path $LockPath -Value $RunId -Encoding UTF8

$StartedAt = Get-Date
$Deadline = $StartedAt.AddHours($DurationHours)
$TickIndex = 0
$NightRanDate = $null

try {
    Log-Line "JARVIS AUTOLOOP 24/7 STARTED" "Green"
    Log-Line "RunId: $RunId" "Green"
    Log-Line "DurationHours: $DurationHours | TickSeconds: $TickSeconds | GatewayLimit: $GatewayLimit" "Green"
    Log-Line "EnableNightMode: $EnableNightMode | DryRun: $DryRun" "Green"

    while ((Get-Date) -lt $Deadline) {
        if (Test-Path $StopPath) {
            Log-Line "Stop file detected. Exiting gracefully." "Yellow"
            break
        }

        $TickIndex += 1
        Log-Line "=== TICK $TickIndex ===" "Cyan"

        $BackendOk = Ensure-Backend

        $TimeContext = Get-TimeContext
        if ($null -ne $TimeContext) {
            Log-Line "Time: $($TimeContext.local) | $($TimeContext.weekday) | $($TimeContext.day_part)" "Cyan"
        } else {
            Log-Line "Time context unavailable." "Yellow"
        }

        if ($BackendOk) {
            Invoke-TimeTick
            Invoke-SafeGateway
        }

        if ($EnableNightMode -and $null -ne $TimeContext) {
            $Hour = [int]$TimeContext.hour
            $Date = [string]$TimeContext.date

            $InNightWindow = $false
            if ($NightStartHour -lt $NightEndHour) {
                $InNightWindow = ($Hour -ge $NightStartHour -and $Hour -lt $NightEndHour)
            } else {
                $InNightWindow = ($Hour -ge $NightStartHour -or $Hour -lt $NightEndHour)
            }

            if ($InNightWindow -and $NightRanDate -ne $Date) {
                Invoke-NightModeOnce
                $NightRanDate = $Date
            }
        }

        $State = @{
            run_id = $RunId
            tick_index = $TickIndex
            started_at = $StartedAt.ToString("o")
            deadline = $Deadline.ToString("o")
            last_tick_at = (Get-Date).ToString("o")
            backend_ok = $BackendOk
            night_ran_date = $NightRanDate
            run_dir = $RunDir
        }

        Save-State $State

        Log-Line "Sleeping $TickSeconds seconds..." "DarkGray"
        Start-Sleep -Seconds $TickSeconds
    }

    Log-Line "JARVIS AUTOLOOP 24/7 FINISHED" "Green"
}
finally {
    Remove-Item $LockPath -Force -ErrorAction SilentlyContinue
    Log-Line "Lock removed. RunDir: $RunDir" "Green"
}