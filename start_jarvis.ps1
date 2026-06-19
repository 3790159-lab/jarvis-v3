<#
.SYNOPSIS
    Unified Jarvis launcher: starts backend (port 8010) and Telegram bot in separate windows.
.PARAMETER BackendOnly  Start only the FastAPI backend.
.PARAMETER BotOnly      Start only the Telegram bot.
.PARAMETER Port         Backend port (default 8010).
.EXAMPLE
    .\start_jarvis.ps1
    .\start_jarvis.ps1 -BackendOnly
    .\start_jarvis.ps1 -BotOnly
#>
param(
    [switch]$BackendOnly,
    [switch]$BotOnly,
    [int]$Port = 8010,
    [string]$BindHost = "127.0.0.1",
    [switch]$Detached,          # headless: hidden processes + redirected logs (autostart)
    [switch]$RegisterAutostart  # register the At-Log-On Scheduled Task, then exit
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot

chcp 65001 | Out-Null
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# Load .env and .env.runpod (P2: both files, runpod last so it wins on conflict).
# RunPod keys (RUNPOD_*/COMFYUI_*) live in .env.runpod; without this the bot and
# any -BotOnly watchdog restart come up unable to reach RunPod.
foreach ($envName in @(".env", ".env.runpod")) {
    $EnvFile = Join-Path $ProjectRoot $envName
    if (Test-Path $EnvFile) {
        Get-Content $EnvFile -Encoding UTF8 | ForEach-Object {
            if ($_ -match '^\s*([^#=][^=]*)=(.*)$') {
                $k = $Matches[1].Trim()
                $v = $Matches[2].Trim().Trim('"').Trim("'")
                [System.Environment]::SetEnvironmentVariable($k, $v, "Process")
            }
        }
        Write-Host "[INFO] Loaded $envName"
    }
}

# Resolve Python executable
$VenvPy = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (Test-Path $VenvPy) { $PythonExe = $VenvPy } else { $PythonExe = "python" }
Write-Host "[INFO] Python: $PythonExe"

$env:PYTHONUTF8       = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONPATH       = $ProjectRoot
$env:BACKEND_BASE_URL = "http://$($BindHost):$Port"

# Ensure logs dir exists (detached mode redirects here).
$LogDir = Join-Path $ProjectRoot "logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

# ---- register autostart task and exit --------------------------------------
# Mirrors scripts\daily_backup.ps1 -RegisterTask. At Log On of the current user
# (+30s settle delay) runs THIS script with -Detached -BotOnly, launching the
# Telegram bot headless with logs. The backend is NOT started here: it is owned
# by the JarvisBackendGuardian task (S4U/Highest, session-independent, with
# crash-restart). Registration does NOT launch anything.
if ($RegisterAutostart) {
    $psArgs = "-NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$ProjectRoot\start_jarvis.ps1`" -Detached -BotOnly"
    $Action  = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $psArgs -WorkingDirectory $ProjectRoot
    $Trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERNAME"
    $Trigger.Delay = "PT30S"
    $Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
        -MultipleInstances IgnoreNew
    $Settings.DisallowStartIfOnBatteries = $false
    $Settings.StopIfGoingOnBatteries     = $false
    Register-ScheduledTask -TaskName "JarvisAutostart" -Action $Action -Trigger $Trigger `
        -Settings $Settings -RunLevel Limited -Force | Out-Null
    Write-Host "[OK] Registered Scheduled Task 'JarvisAutostart' (At Log On of $env:USERNAME, +30s, runs: start_jarvis.ps1 -Detached -BotOnly; backend owned by JarvisBackendGuardian)"
    return
}

# Detached launcher: hidden process + redirected logs (used by the autostart task
# and any headless start). Env (incl. PYTHONUTF8/PYTHONIOENCODING) is already set
# at process scope above, so these children inherit the UTF-8 bake.
function Start-JarvisDetached {
    param([string]$ExePath, [string[]]$ExeArgs, [string]$LogName)
    $ts  = Get-Date -Format "yyyyMMdd_HHmmss"
    $out = Join-Path $LogDir "$($LogName)_$ts.out.log"
    $err = Join-Path $LogDir "$($LogName)_$ts.err.log"
    $p = Start-Process -FilePath $ExePath -ArgumentList $ExeArgs -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
    Write-Host "[INFO] $LogName detached PID=$($p.Id) -> $out"
    return $p
}

function Start-JarvisWindow {
    param([string]$ScriptPath, [string]$Title = "Jarvis")

    # Method 1: Windows Terminal вЂ” creates visible, titled tab
    $wtExe = "$env:LOCALAPPDATA\Microsoft\WindowsApps\wt.exe"
    if (-not (Test-Path $wtExe)) { $wtExe = "$env:ProgramFiles\WindowsApps\Microsoft.WindowsTerminal*\wt.exe" | Resolve-Path -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Path }
    if ($wtExe -and (Test-Path $wtExe)) {
        Start-Process $wtExe -ArgumentList "new-tab", "--title", "`"$Title`"", "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-NoExit", "-File", "`"$ScriptPath`""
        return
    }

    # Method 2: cmd /c start вЂ” always creates a true new console window
    $esc = $ScriptPath -replace '"', '\"'
    Start-Process cmd.exe -ArgumentList "/c", "start", "`"$Title`"", "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-NoExit", "-File", "`"$esc`""
}

function Write-TempScript {
    param([string]$Content)
    $tmp = [System.IO.Path]::GetTempFileName() + ".ps1"
    [System.IO.File]::WriteAllText($tmp, $Content, [System.Text.Encoding]::UTF8)
    return $tmp
}

function Stop-OldBot {
    # Kill any existing Telegram bot before starting a new one. The bot enforces
    # single-instance via state\bot.pid, so a stale bot (e.g. one stuck on a long
    # swap that the watchdog wants to restart) would make the new instance exit
    # immediately. Killing it first is what makes -BotOnly restart actually work.
    Write-Host "[INFO] Stopping any existing Telegram bot..."
    $pidFile = Join-Path $ProjectRoot "state\bot.pid"

    # 1) By recorded PID.
    if (Test-Path $pidFile) {
        try {
            $oldPid = [int]((Get-Content $pidFile -ErrorAction Stop) -join "").Trim()
            if (Get-Process -Id $oldPid -ErrorAction SilentlyContinue) {
                Stop-Process -Id $oldPid -Force -ErrorAction SilentlyContinue
                Write-Host "[INFO] Stopped bot PID $oldPid (from pid file)"
            }
        } catch { }
    }

    # 2) By command line — covers a stale/missing pid file.
    try {
        $procs = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -and $_.CommandLine -match 'jarvis_smart_telegram_control' }
        foreach ($p in $procs) {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
            Write-Host "[INFO] Stopped bot process $($p.ProcessId) (by command line)"
        }
    } catch { }

    # Clear the stale pid file so the fresh instance starts clean.
    if (Test-Path $pidFile) { Remove-Item $pidFile -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 500
}

# ---- backend ----------------------------------------------------------------
if (-not $BotOnly) {
    $MainFile = Join-Path $ProjectRoot "app\main.py"
    & $PythonExe -m py_compile $MainFile
    if ($LASTEXITCODE -ne 0) { throw "Syntax error in app\main.py" }
    Write-Host "[INFO] Backend syntax OK"

    $lines = @(
        "chcp 65001 | Out-Null",
        '[Console]::OutputEncoding = [System.Text.Encoding]::UTF8',
        '$env:PYTHONUTF8 = "1"',
        '$env:PYTHONIOENCODING = "utf-8"',
        ('$env:PYTHONPATH = "' + $ProjectRoot + '"'),
        ('$env:BACKEND_BASE_URL = "http://' + $BindHost + ':' + $Port + '"'),
        ('Set-Location "' + $ProjectRoot + '"'),
        ('Write-Host "[INFO] Starting FastAPI backend on ' + $BindHost + ':' + $Port + '..."'),
        ('& "' + $PythonExe + '" -m uvicorn app.main:app --host ' + $BindHost + ' --port ' + $Port),
        'Read-Host "Backend stopped. Press Enter to close"'
    )
    if ($Detached) {
        Start-JarvisDetached -ExePath $PythonExe `
            -ExeArgs @("-m","uvicorn","app.main:app","--host",$BindHost,"--port","$Port") `
            -LogName "backend_boot" | Out-Null
    } else {
        $backendScript = Write-TempScript ($lines -join "`r`n")
        Start-JarvisWindow -ScriptPath $backendScript -Title "Jarvis Backend :$Port"
        Write-Host "[INFO] Backend window launched"
    }

    # Phase I.5: Wait for backend to be ready before starting bot
    Write-Host "[INFO] Waiting for backend to be ready..."
    $maxAttempts = 30
    $attempt = 0
    $backendReady = $false
    while ($attempt -lt $maxAttempts) {
        try {
            $resp = Invoke-WebRequest -UseBasicParsing -Uri "http://$($BindHost):$Port/health" -TimeoutSec 2 -ErrorAction Stop
            if ($resp.StatusCode -eq 200) {
                Write-Host "[OK] Backend ready" -ForegroundColor Green
                $backendReady = $true
                break
            }
        } catch {
            Start-Sleep -Seconds 1
            $attempt++
        }
    }
    if (-not $backendReady) {
        Write-Host "[WARN] Backend not ready after 30s -- starting bot anyway" -ForegroundColor Yellow
    }
}

# ---- telegram bot -----------------------------------------------------------
if (-not $BackendOnly) {
    Stop-OldBot

    $BotFile = Join-Path $ProjectRoot "tools\jarvis_smart_telegram_control.py"
    & $PythonExe -m py_compile $BotFile
    if ($LASTEXITCODE -ne 0) { throw "Syntax error in tools\jarvis_smart_telegram_control.py" }
    Write-Host "[INFO] Bot syntax OK"

    if (-not $env:TELEGRAM_BOT_TOKEN) {
        Write-Warning "TELEGRAM_BOT_TOKEN not set - bot will exit immediately"
    }

    $lines2 = @(
        "chcp 65001 | Out-Null",
        '[Console]::OutputEncoding = [System.Text.Encoding]::UTF8',
        '$env:PYTHONUTF8 = "1"',
        '$env:PYTHONIOENCODING = "utf-8"',
        ('$env:PYTHONPATH = "' + $ProjectRoot + '"'),
        ('$env:BACKEND_BASE_URL = "http://' + $BindHost + ':' + $Port + '"'),
        ('Set-Location "' + $ProjectRoot + '"'),
        'Write-Host "[INFO] Starting Jarvis Telegram bot..."',
        ('& "' + $PythonExe + '" "' + $BotFile + '"'),
        'Read-Host "Bot stopped. Press Enter to close"'
    )
    if ($Detached) {
        Start-JarvisDetached -ExePath $PythonExe -ExeArgs @("$BotFile") -LogName "bot_boot" | Out-Null
    } else {
        $botScript = Write-TempScript ($lines2 -join "`r`n")
        Start-JarvisWindow -ScriptPath $botScript -Title "Jarvis Telegram Bot"
        Write-Host "[INFO] Bot window launched"
    }
}

Write-Host ""
Write-Host "[OK] Jarvis is up."
Write-Host "     Backend : http://$($BindHost):$Port/health"
Write-Host "     Bot file: tools\jarvis_smart_telegram_control.py"
