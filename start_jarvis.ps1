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
    [string]$BindHost = "127.0.0.1"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot

chcp 65001 | Out-Null
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# Load .env
$EnvFile = Join-Path $ProjectRoot ".env"
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        if ($_ -match '^\s*([^#=][^=]*)=(.*)$') {
            $k = $Matches[1].Trim()
            $v = $Matches[2].Trim().Trim('"').Trim("'")
            [System.Environment]::SetEnvironmentVariable($k, $v, "Process")
        }
    }
    Write-Host "[INFO] Loaded .env"
}

# Resolve Python executable
$VenvPy = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (Test-Path $VenvPy) { $PythonExe = $VenvPy } else { $PythonExe = "python" }
Write-Host "[INFO] Python: $PythonExe"

$env:PYTHONUTF8       = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONPATH       = $ProjectRoot
$env:BACKEND_BASE_URL = "http://$($BindHost):$Port"

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
    $backendScript = Write-TempScript ($lines -join "`r`n")
    Start-JarvisWindow -ScriptPath $backendScript -Title "Jarvis Backend :$Port"
    Write-Host "[INFO] Backend window launched"

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
    $botScript = Write-TempScript ($lines2 -join "`r`n")
    Start-JarvisWindow -ScriptPath $botScript -Title "Jarvis Telegram Bot"
    Write-Host "[INFO] Bot window launched"
}

Write-Host ""
Write-Host "[OK] Jarvis is up."
Write-Host "     Backend : http://$($BindHost):$Port/health"
Write-Host "     Bot file: tools\jarvis_smart_telegram_control.py"
