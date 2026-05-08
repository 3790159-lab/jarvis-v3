[CmdletBinding()]
param(
    [string]$ProjectRoot = (Get-Location).Path,
    [int]$Port = 8010,
    [int]$CheckIntervalSeconds = 5,
    [int]$HealthAttempts = 12,
    [int]$HealthDelaySeconds = 2,
    [int]$MaxConsecutiveFailures = 5,
    [int]$DegradedCyclesBeforeRestart = 12,
    [int]$MinimumRestartIntervalSeconds = 45
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

$ArtifactsDir    = Join-Path $ProjectRoot "artifacts"
$LogsDir         = Join-Path $ArtifactsDir "logs"
$GuardStateFile  = Join-Path $ArtifactsDir "guard_state.json"
$GuardLogFile    = Join-Path $LogsDir "guard.log"
$GuardLockFile   = Join-Path $ArtifactsDir "server_guard.lock"

function Ensure-Dir {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
}

Ensure-Dir -Path $ArtifactsDir
Ensure-Dir -Path $LogsDir

function Write-Log {
    param(
        [string]$Message,
        [ValidateSet("INFO","WARN","ERROR")]
        [string]$Level = "INFO"
    )

    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[{0}][{1}] {2}" -f $ts, $Level, $Message
    Write-Host $line
    Add-Content -Path $GuardLogFile -Value $line -Encoding UTF8
}

function Write-JsonAtomic {
    param(
        [string]$Path,
        [object]$Object
    )

    $tmp = "{0}.{1}.tmp" -f $Path, ([guid]::NewGuid().ToString("N"))
    $Object | ConvertTo-Json -Depth 12 | Set-Content -Path $tmp -Encoding UTF8
    Move-Item -Path $tmp -Destination $Path -Force
}

function Write-GuardState {
    param(
        [int]$GuardPid,
        [object]$ChildPid,
        [string]$Status,
        [int]$RestartCount,
        [string]$Reason = ""
    )

    $state = [ordered]@{
        updated_at    = (Get-Date).ToString("s")
        project_root  = $ProjectRoot
        port          = $Port
        guard_pid     = $GuardPid
        child_pid     = $ChildPid
        status        = $Status
        restart_count = $RestartCount
        reason        = $Reason
    }

    Write-JsonAtomic -Path $GuardStateFile -Object $state
}

function Write-GuardLock {
    param(
        [int]$GuardPid,
        [object]$ChildPid
    )

    $lock = [ordered]@{
        project_root = $ProjectRoot
        port         = $Port
        guard_pid    = $GuardPid
        child_pid    = $ChildPid
        updated_at   = (Get-Date).ToString("s")
    }

    Write-JsonAtomic -Path $GuardLockFile -Object $lock
}

function Resolve-PythonExe {
    $venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        return $venvPython
    }

    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($null -ne $pythonCmd) {
        return $pythonCmd.Source
    }

    throw "Python not found."
}

function Test-HealthEndpoint {
    try {
        return Invoke-RestMethod -Uri ("http://127.0.0.1:{0}/health" -f $Port) -TimeoutSec 5
    }
    catch {
        return $null
    }
}

function Test-PidAlive {
    param([object]$PidValue)

    if ($null -eq $PidValue) {
        return $false
    }

    try {
        $null = Get-Process -Id ([int]$PidValue) -ErrorAction Stop
        return $true
    }
    catch {
        return $false
    }
}

function Get-ProjectPythonProcesses {
    $all = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -eq "python.exe" -and
        $null -ne $_.CommandLine -and
        (
            $_.CommandLine -like "*uvicorn*app.main:app*" -or
            $_.CommandLine -like "*run_api_server.py*"
        ) -and
        $_.CommandLine -like ("*{0}*" -f $ProjectRoot)
    }
    return @($all)
}

function Get-ProjectPortOwners {
    try {
        $connections = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
        if ($null -eq $connections) {
            return @()
        }
        return @($connections | Select-Object -ExpandProperty OwningProcess -Unique | Where-Object { $_ -gt 0 })
    }
    catch {
        return @()
    }
}

function Clear-PortIfNeeded {
    $owners = @(Get-ProjectPortOwners)
    if ($owners.Count -eq 0) {
        return
    }

    foreach ($pidToCheck in $owners) {
        $proc = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $pidToCheck) -ErrorAction SilentlyContinue
        if ($null -eq $proc) {
            continue
        }

        $isProjectPython = (
            $proc.Name -eq "python.exe" -and
            $null -ne $proc.CommandLine -and
            (
                $proc.CommandLine -like "*uvicorn*app.main:app*" -or
                $proc.CommandLine -like "*run_api_server.py*"
            ) -and
            $proc.CommandLine -like ("*{0}*" -f $ProjectRoot)
        )

        if (-not $isProjectPython) {
            throw ("Port {0} is occupied by non-project PID={1}. Refusing to kill foreign process." -f $Port, $pidToCheck)
        }
    }

    foreach ($pidToStop in $owners) {
        try {
            Stop-Process -Id $pidToStop -Force -ErrorAction Stop
            Write-Log -Message ("Stopped project process on port {0}. PID={1}" -f $Port, $pidToStop) -Level "WARN"
        }
        catch {
            Write-Log -Message ("Could not stop PID={0} on port {1}. {2}" -f $pidToStop, $Port, $_.Exception.Message) -Level "WARN"
        }
    }

    Start-Sleep -Seconds 2
}

function Wait-ForHealth {
    param(
        [int]$Attempts = 12,
        [int]$DelaySeconds = 2
    )

    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        $health = Test-HealthEndpoint
        if ($null -ne $health) {
            Write-Log -Message ("Health endpoint is ready on attempt {0}." -f $attempt)
            return $health
        }

        Write-Log -Message ("Health check attempt {0}/{1} failed. Waiting {2} sec..." -f $attempt, $Attempts, $DelaySeconds) -Level "WARN"
        Start-Sleep -Seconds $DelaySeconds
    }

    return $null
}

function Test-Bootstrap {
    if (-not (Test-Path $ProjectRoot)) {
        throw ("ProjectRoot not found: {0}" -f $ProjectRoot)
    }

    $appMain = Join-Path $ProjectRoot "app\main.py"
    if (-not (Test-Path $appMain)) {
        throw ("app.main source not found: {0}" -f $appMain)
    }

    $pythonExe = Resolve-PythonExe
    Push-Location $ProjectRoot
    try {
        & $pythonExe -c "import app.main; import uvicorn; print('IMPORT_OK')" 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "app.main or uvicorn import failed"
        }
    }
    finally {
        Pop-Location
    }

    return $true
}

function Start-ApiChild {
    $pythonExe = Resolve-PythonExe
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $stdoutLog = Join-Path $LogsDir ("api_stdout_{0}.log" -f $timestamp)
    $stderrLog = Join-Path $LogsDir ("api_stderr_{0}.log" -f $timestamp)

    Clear-PortIfNeeded

    $argList = @(
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        ("{0}" -f $Port),
        "--workers",
        "1",
        "--lifespan",
        "on",
        "--log-level",
        "info"
    )

    Write-Log -Message ("Starting API child via uvicorn module with python: {0}" -f $pythonExe)

    $child = Start-Process `
        -FilePath $pythonExe `
        -ArgumentList $argList `
        -WorkingDirectory $ProjectRoot `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog `
        -PassThru `
        -WindowStyle Hidden

    Start-Sleep -Seconds 3

    try {
        $null = Get-Process -Id $child.Id -ErrorAction Stop
    }
    catch {
        $stderr = if (Test-Path $stderrLog) { Get-Content $stderrLog -Raw -Encoding UTF8 } else { "" }
        $stdout = if (Test-Path $stdoutLog) { Get-Content $stdoutLog -Raw -Encoding UTF8 } else { "" }

        $message = "Child process exited immediately after start."
        if ($stderr) { $message += "`n--- STDERR ---`n$stderr" }
        if ($stdout) { $message += "`n--- STDOUT ---`n$stdout" }

        throw $message
    }

    return $child
}

$currentGuardPid = $PID
$restartCount = 0
$consecutiveFailures = 0
$degradedCycles = 0
$currentChildPid = $null
$lastStartAt = $null

try {
    Test-Bootstrap | Out-Null
    Write-Log -Message "Bootstrap check passed."
    Write-GuardState -GuardPid $currentGuardPid -ChildPid $null -Status "starting" -RestartCount 0 -Reason ""
    Write-GuardLock -GuardPid $currentGuardPid -ChildPid $null
}
catch {
    $msg = $_.Exception.Message
    Write-Log -Message ("Bootstrap failed: {0}" -f $msg) -Level "ERROR"
    Write-GuardState -GuardPid $currentGuardPid -ChildPid $null -Status "bootstrap_failed" -RestartCount 0 -Reason $msg
    Write-GuardLock -GuardPid $currentGuardPid -ChildPid $null
    throw
}

while ($true) {
    try {
        if ($null -ne $currentChildPid -and (Test-PidAlive -PidValue $currentChildPid)) {
            $health = Test-HealthEndpoint

            if ($null -ne $health) {
                Write-GuardState -GuardPid $currentGuardPid -ChildPid $currentChildPid -Status "running" -RestartCount $restartCount -Reason ""
                Write-GuardLock -GuardPid $currentGuardPid -ChildPid $currentChildPid
                $consecutiveFailures = 0
                $degradedCycles = 0
                Start-Sleep -Seconds $CheckIntervalSeconds
                continue
            }

            $degradedCycles++
            Write-Log -Message ("Sticky child {0} alive but health unavailable. degraded_cycle={1}" -f $currentChildPid, $degradedCycles) -Level "WARN"
            Write-GuardState -GuardPid $currentGuardPid -ChildPid $currentChildPid -Status "degraded" -RestartCount $restartCount -Reason "alive_process_health_missing"
            Write-GuardLock -GuardPid $currentGuardPid -ChildPid $currentChildPid

            $canRestartNow = $true
            if ($null -ne $lastStartAt) {
                $elapsed = ((Get-Date) - $lastStartAt).TotalSeconds
                if ($elapsed -lt $MinimumRestartIntervalSeconds) {
                    $canRestartNow = $false
                    Write-Log -Message ("Restart suppressed. Only {0:N1}s since last start, minimum is {1}s." -f $elapsed, $MinimumRestartIntervalSeconds) -Level "WARN"
                }
            }

            if ($degradedCycles -lt $DegradedCyclesBeforeRestart -or -not $canRestartNow) {
                Start-Sleep -Seconds $CheckIntervalSeconds
                continue
            }

            Write-Log -Message ("Restarting sticky child PID={0}" -f $currentChildPid) -Level "WARN"
            try {
                Stop-Process -Id ([int]$currentChildPid) -Force -ErrorAction Stop
            }
            catch {}
            Start-Sleep -Seconds 2
            $currentChildPid = $null
        }

        # Try adopting one existing project process, but do not kill others here
        $projectProcs = @(Get-ProjectPythonProcesses)
        if ($projectProcs.Count -eq 1) {
            $candidatePid = [int]$projectProcs[0].ProcessId
            if (Test-PidAlive -PidValue $candidatePid) {
                $currentChildPid = $candidatePid
                Write-Log -Message ("Adopted existing project child PID={0}" -f $currentChildPid)
                Start-Sleep -Seconds $CheckIntervalSeconds
                continue
            }
        }

        Write-Log -Message "No usable child found. Starting fresh API child..." -Level "WARN"
        $child = Start-ApiChild
        $currentChildPid = [int]$child.Id
        $restartCount++
        $lastStartAt = Get-Date

        $health = Wait-ForHealth -Attempts $HealthAttempts -DelaySeconds $HealthDelaySeconds
        if ($null -eq $health) {
            throw "Health endpoint did not become ready."
        }

        Write-GuardState -GuardPid $currentGuardPid -ChildPid $currentChildPid -Status "running" -RestartCount $restartCount -Reason ""
        Write-GuardLock -GuardPid $currentGuardPid -ChildPid $currentChildPid
        $consecutiveFailures = 0
        $degradedCycles = 0

        Start-Sleep -Seconds $CheckIntervalSeconds
    }
    catch {
        $consecutiveFailures++
        $reason = $_.Exception.Message

        Write-Log -Message ("Guard loop error: {0}" -f $reason) -Level "ERROR"
        Write-GuardState -GuardPid $currentGuardPid -ChildPid $currentChildPid -Status "error" -RestartCount $restartCount -Reason $reason
        Write-GuardLock -GuardPid $currentGuardPid -ChildPid $currentChildPid

        if ($consecutiveFailures -ge $MaxConsecutiveFailures) {
            Write-Log -Message ("Too many consecutive failures ({0}). Guard is stopping." -f $consecutiveFailures) -Level "ERROR"
            break
        }

        Start-Sleep -Seconds 3
    }
}
