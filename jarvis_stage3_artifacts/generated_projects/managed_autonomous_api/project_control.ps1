[CmdletBinding()]
param(
    [ValidateSet("start","stop","restart","status","purge","repair","snapshot","selftest","heal","doctor","dedupe","readycheck")]
    [string]$Action = "status",
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

$ArtifactsDir   = Join-Path $ProjectRoot "artifacts"
$LogsDir        = Join-Path $ArtifactsDir "logs"
$GuardScript    = Join-Path $ProjectRoot "managed_api_guard.ps1"
$RunApiScript   = Join-Path $ProjectRoot "run_api_server.py"
$GuardStateFile = Join-Path $ArtifactsDir "guard_state.json"
$GuardLockFile  = Join-Path $ArtifactsDir "server_guard.lock"
$RepairReport   = Join-Path $ProjectRoot "state\repair_report.json"
$RuntimeLog     = Join-Path $ProjectRoot "runtime.log"

function Ensure-Dir {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
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

function Read-JsonFile {
    param([string]$Path)

    if (-not (Test-Path $Path)) {
        return $null
    }

    try {
        return (Get-Content -Path $Path -Raw -Encoding UTF8 | ConvertFrom-Json)
    }
    catch {
        return $null
    }
}

function Get-Health {
    try {
        return Invoke-RestMethod -Uri ("http://127.0.0.1:{0}/health" -f $Port) -TimeoutSec 5
    }
    catch {
        return $null
    }
}

function Get-ProjectPythonProcesses {
    $all = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -eq "python.exe" -and
        $null -ne $_.CommandLine -and
        $_.CommandLine -like "*run_api_server.py*" -and
        $_.CommandLine -like ("*{0}*" -f $ProjectRoot)
    }

    return @($all)
}

function Get-GuardPowerShellProcesses {
    $all = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -like "powershell*.exe" -and
        $null -ne $_.CommandLine -and
        $_.CommandLine -like "*managed_api_guard.ps1*" -and
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

function Stop-ProjectProcesses {
    $guardState = Read-JsonFile -Path $GuardStateFile

    if ($null -ne $guardState) {
        if ($null -ne $guardState.child_pid) {
            try {
                Stop-Process -Id ([int]$guardState.child_pid) -Force -ErrorAction Stop
                Write-Host ("Stopped child PID={0}" -f $guardState.child_pid)
            }
            catch {}
        }

        if ($null -ne $guardState.guard_pid) {
            try {
                Stop-Process -Id ([int]$guardState.guard_pid) -Force -ErrorAction Stop
                Write-Host ("Stopped guard PID={0}" -f $guardState.guard_pid)
            }
            catch {}
        }
    }

    foreach ($proc in (Get-ProjectPythonProcesses)) {
        try {
            Stop-Process -Id ([int]$proc.ProcessId) -Force -ErrorAction Stop
            Write-Host ("Stopped project python PID={0}" -f $proc.ProcessId)
        }
        catch {}
    }

    foreach ($proc in (Get-GuardPowerShellProcesses)) {
        try {
            Stop-Process -Id ([int]$proc.ProcessId) -Force -ErrorAction Stop
            Write-Host ("Stopped guard powershell PID={0}" -f $proc.ProcessId)
        }
        catch {}
    }
}

function Repair-ProjectState {
    Ensure-Dir -Path $ArtifactsDir
    Ensure-Dir -Path $LogsDir
    Ensure-Dir -Path (Join-Path $ProjectRoot "state")

    $pythonExe = Resolve-PythonExe

    if (-not (Test-Path $RunApiScript)) {
        throw ("Missing run_api_server.py at {0}" -f $RunApiScript)
    }

    if (-not (Test-Path $GuardScript)) {
        throw ("Missing managed_api_guard.ps1 at {0}" -f $GuardScript)
    }

    Push-Location $ProjectRoot
    try {
        & $pythonExe -m py_compile $RunApiScript 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "run_api_server.py failed py_compile"
        }

        & $pythonExe -c "import app.main; print('IMPORT_OK')" 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "app.main import failed"
        }
    }
    finally {
        Pop-Location
    }

    $report = [ordered]@{
        repaired_at  = (Get-Date).ToString("s")
        project_root = $ProjectRoot
        guard_script = (Test-Path $GuardScript)
        run_api      = (Test-Path $RunApiScript)
        artifacts    = (Test-Path $ArtifactsDir)
        logs         = (Test-Path $LogsDir)
        python       = $pythonExe
        status       = "ok"
    }

    $report | ConvertTo-Json -Depth 10 | Set-Content -Path $RepairReport -Encoding UTF8
    Write-Host "Repair check complete."
}

function Get-GuardOutputTail {
    param([string]$Path)

    if (-not (Test-Path $Path)) {
        return ""
    }

    try {
        return (Get-Content $Path -Tail 80 -ErrorAction Stop) -join "`n"
    }
    catch {
        return ""
    }
}

function Start-Guard {
    Repair-ProjectState

    $existingHealth = Get-Health
    if ($null -ne $existingHealth) {
        Write-Host ("Service already healthy on port {0}. No new start required." -f $Port)
        return
    }

    $existingGuard = @(Get-GuardPowerShellProcesses)
    if ($existingGuard.Count -gt 0) {
        Write-Host "Stale guard process(es) detected. Stopping them first."
        Stop-ProjectProcesses
        Start-Sleep -Seconds 2
    }

    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $guardStdout = Join-Path $LogsDir ("guard_start_stdout_{0}.log" -f $timestamp)
    $guardStderr = Join-Path $LogsDir ("guard_start_stderr_{0}.log" -f $timestamp)

    $argLine = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -ProjectRoot "{1}" -Port {2}' -f $GuardScript, $ProjectRoot, $Port
    $guardProc = Start-Process `
        -FilePath "powershell.exe" `
        -ArgumentList $argLine `
        -WorkingDirectory $ProjectRoot `
        -RedirectStandardOutput $guardStdout `
        -RedirectStandardError $guardStderr `
        -PassThru `
        -WindowStyle Hidden

    Write-Host ("Started guard PID={0}" -f $guardProc.Id)
    Write-Host ("Guard stdout log: {0}" -f $guardStdout)
    Write-Host ("Guard stderr log: {0}" -f $guardStderr)

    Start-Sleep -Seconds 2

    $guardAlive = $true
    try {
        $null = Get-Process -Id $guardProc.Id -ErrorAction Stop
    }
    catch {
        $guardAlive = $false
    }

    if (-not $guardAlive) {
        $stderr = Get-GuardOutputTail -Path $guardStderr
        $stdout = Get-GuardOutputTail -Path $guardStdout

        $message = "Guard process exited immediately after start."
        if ($stderr) { $message += "`n--- GUARD STDERR ---`n$stderr" }
        if ($stdout) { $message += "`n--- GUARD STDOUT ---`n$stdout" }

        throw $message
    }

    $ready = $false
    for ($i = 1; $i -le 20; $i++) {
        Start-Sleep -Seconds 2
        $health = Get-Health
        if ($null -ne $health) {
            $ready = $true
            break
        }
    }

    if (-not $ready) {
        $stderr = Get-GuardOutputTail -Path $guardStderr
        $stdout = Get-GuardOutputTail -Path $guardStdout
        $guardState = Read-JsonFile -Path $GuardStateFile
        $guardLock = Read-JsonFile -Path $GuardLockFile

        Write-Host "Guard state after failed start:"
        if ($null -ne $guardState) { $guardState | ConvertTo-Json -Depth 10 } else { Write-Host "guard_state.json not found" }

        Write-Host "Guard lock after failed start:"
        if ($null -ne $guardLock) { $guardLock | ConvertTo-Json -Depth 10 } else { Write-Host "server_guard.lock not found" }

        $message = "Service did not become healthy after guard start."
        if ($stderr) { $message += "`n--- GUARD STDERR ---`n$stderr" }
        if ($stdout) { $message += "`n--- GUARD STDOUT ---`n$stdout" }

        throw $message
    }

    Write-Host ("Service healthy. PID={0}" -f $health.pid)
}

function Get-SystemSnapshot {
    $health = Get-Health
    $pythonProcesses = @(Get-ProjectPythonProcesses)
    $guardProcesses = @(Get-GuardPowerShellProcesses)
    $portOwners = @(Get-ProjectPortOwners)
    $guardState = Read-JsonFile -Path $GuardStateFile
    $guardLock  = Read-JsonFile -Path $GuardLockFile

    $canonicalPid = $null

    if ($null -ne $health -and ($health.PSObject.Properties.Name -contains "pid")) {
        $canonicalPid = [int]$health.pid
    } elseif ($portOwners.Count -eq 1) {
        $canonicalPid = [int]$portOwners[0]
    } elseif ($pythonProcesses.Count -eq 1) {
        $canonicalPid = [int]$pythonProcesses[0].ProcessId
    }

    return [ordered]@{
        checked_at        = (Get-Date).ToString("s")
        project_root      = $ProjectRoot
        port              = $Port
        canonical_pid     = $canonicalPid
        health            = $health
        guard_state       = $guardState
        guard_lock        = $guardLock
        python_processes  = @($pythonProcesses | Select-Object ProcessId, Name, CommandLine)
        guard_processes   = @($guardProcesses | Select-Object ProcessId, Name, CommandLine)
        port_owners       = @($portOwners)
        files             = [ordered]@{
            guard_script_exists  = (Test-Path $GuardScript)
            run_api_exists       = (Test-Path $RunApiScript)
            repair_report_exists = (Test-Path $RepairReport)
            runtime_log_exists   = (Test-Path $RuntimeLog)
        }
    }
}

function Invoke-Dedupe {
    $snapshot = Get-SystemSnapshot
    $keepPid = $snapshot.canonical_pid

    if ($null -eq $keepPid) {
        Write-Host "Canonical PID not resolved. Refusing to dedupe blindly."
        return
    }

    $pythonProcs = @($snapshot.python_processes)
    if ($pythonProcs.Count -le 1) {
        Write-Host "No duplicate python processes found."
        return
    }

    foreach ($proc in $pythonProcs) {
        $pidValue = [int]$proc.ProcessId
        if ($pidValue -eq $keepPid) {
            continue
        }

        try {
            Stop-Process -Id $pidValue -Force -ErrorAction Stop
            Write-Host ("Stopped duplicate project python PID={0}" -f $pidValue)
        }
        catch {
            Write-Host ("Could not stop duplicate PID={0}" -f $pidValue)
        }
    }
}

function Invoke-DebugDoctorObject {
    $snapshot = Get-SystemSnapshot
    $health = $snapshot.health
    $guardState = $snapshot.guard_state
    $guardLock = $snapshot.guard_lock
    $pyProcs = @($snapshot.python_processes)
    $guardProcs = @($snapshot.guard_processes)
    $portOwners = @($snapshot.port_owners)
    $canonicalPid = $snapshot.canonical_pid

    $issues = @()

    if ($null -eq $health) { $issues += "health_unavailable" }
    if ($null -eq $guardState) { $issues += "missing_guard_state" }
    if ($null -eq $guardLock) { $issues += "missing_guard_lock" }
    if ($pyProcs.Count -gt 1) { $issues += "duplicate_python_processes" }
    if ($guardProcs.Count -gt 1) { $issues += "duplicate_guard_processes" }
    if ($null -eq $canonicalPid) { $issues += "canonical_pid_unresolved" }

    if ($null -ne $guardState) {
        if ($null -ne $guardState.child_pid -and -not (Test-PidAlive -PidValue $guardState.child_pid)) {
            $issues += "stale_child_pid_in_state"
        }
        if ($null -ne $guardState.guard_pid -and -not (Test-PidAlive -PidValue $guardState.guard_pid)) {
            $issues += "stale_guard_pid_in_state"
        }
        if ($guardState.status -eq "running" -and $null -eq $health) {
            $issues += "running_without_health"
        }
    }

    if ($null -ne $health -and ($health.PSObject.Properties.Name -contains "pid") -and $null -ne $guardState -and $null -ne $guardState.child_pid) {
        if ([int]$health.pid -ne [int]$guardState.child_pid) {
            $issues += "health_pid_mismatch"
        }
    }

    if ($portOwners.Count -eq 1 -and $null -ne $guardState -and $null -ne $guardState.child_pid) {
        if ([int]$portOwners[0] -ne [int]$guardState.child_pid) {
            $issues += "port_owner_mismatch"
        }
    }

    if ($null -ne $canonicalPid -and $null -ne $guardState -and $null -ne $guardState.child_pid) {
        if ([int]$canonicalPid -ne [int]$guardState.child_pid) {
            $issues += "canonical_pid_mismatch"
        }
    }

    return [ordered]@{
        checked_at        = $snapshot.checked_at
        project_root      = $ProjectRoot
        port              = $Port
        canonical_pid     = $canonicalPid
        health            = $health
        guard_state       = $guardState
        guard_lock        = $guardLock
        python_processes  = $pyProcs
        guard_processes   = $guardProcs
        port_owners       = $portOwners
        files             = $snapshot.files
        issues            = @($issues)
        healthy           = ($issues.Count -eq 0)
    }
}

function Invoke-Doctor {
    $obj = Invoke-DebugDoctorObject
    $obj | ConvertTo-Json -Depth 12
}

function Show-Status {
    $obj = Invoke-DebugDoctorObject
    $obj | ConvertTo-Json -Depth 12
}

function New-Snapshot {
    Ensure-Dir -Path $ArtifactsDir
    $snapshotFile = Join-Path $ArtifactsDir ("snapshot_{0}.json" -f (Get-Date -Format "yyyyMMdd_HHmmss"))
    $snapshot = Invoke-DebugDoctorObject
    $snapshot | ConvertTo-Json -Depth 12 | Set-Content -Path $snapshotFile -Encoding UTF8
    Write-Host ("Snapshot written: {0}" -f $snapshotFile)
}

function Invoke-SelfTest {
    Repair-ProjectState
    Show-Status
}

function Invoke-Heal {
    $doctor = Invoke-DebugDoctorObject
    if ($doctor.healthy) {
        Write-Host "Service is already healthy. Heal not required."
        return
    }

    Write-Host "Doctor found issues:"
    $doctor.issues | ForEach-Object { Write-Host (" - {0}" -f $_) }

    Stop-ProjectProcesses
    Start-Sleep -Seconds 2
    Repair-ProjectState
    Start-Guard
    Invoke-Dedupe
}

switch ($Action) {
    "start"     { Start-Guard }
    "stop"      { Stop-ProjectProcesses; Write-Host "Stop complete." }
    "restart"   { Stop-ProjectProcesses; Start-Sleep -Seconds 2; Start-Guard }
    "status"    { Show-Status }
    "purge"     {
        Stop-ProjectProcesses
        foreach ($path in @($GuardStateFile, $GuardLockFile)) {
            if (Test-Path $path) {
                Remove-Item -Path $path -Force -ErrorAction SilentlyContinue
                Write-Host ("Removed: {0}" -f $path)
            }
        }
        Write-Host "Purge complete."
    }
    "repair"    { Repair-ProjectState }
    "snapshot"  { New-Snapshot }
    "selftest"  { Invoke-SelfTest }
    "heal"      { Invoke-Heal }
    "doctor"    { Invoke-Doctor }
    "dedupe"    { Invoke-Dedupe }
    "readycheck" {
        $health = Get-Health
        if ($null -eq $health) {
            throw "Service not ready."
        }
        Write-Host "READY"
    }
    default     { throw ("Unknown action: {0}" -f $Action) }
}
