# Jarvis Windows Service Installer via NSSM
# Requires: Run as Administrator
# Installs: JarvisBackend (uvicorn) + JarvisBot (telegram) + JarvisWatchdog

param(
    [switch]$NoStart  # Install but don't start services
)

$ErrorActionPreference = "Stop"
$ProjectDir = (Resolve-Path "$PSScriptRoot\..").Path
$NssmDir = "$ProjectDir\nssm"
$NssmExe = "$NssmDir\nssm.exe"
$PythonExe = "$ProjectDir\.venv\Scripts\python.exe"
$NssmUrl = "https://nssm.cc/release/nssm-2.24.zip"
$StateDir = "$ProjectDir\state"

# Ensure state dir exists
if (-not (Test-Path $StateDir)) { New-Item -ItemType Directory -Path $StateDir -Force | Out-Null }
if (-not (Test-Path $NssmDir)) { New-Item -ItemType Directory -Path $NssmDir -Force | Out-Null }

Write-Host "=== Jarvis Service Installer ===" -ForegroundColor Cyan
Write-Host "Project: $ProjectDir"

# ── Download NSSM ──────────────────────────────────────────────────────────────
if (-not (Test-Path $NssmExe)) {
    Write-Host "Downloading NSSM..." -ForegroundColor Yellow
    $ZipPath = "$env:TEMP\nssm.zip"
    $ExtractPath = "$env:TEMP\nssm_extract"

    try {
        Invoke-WebRequest -Uri $NssmUrl -OutFile $ZipPath -UseBasicParsing
        if (Test-Path $ExtractPath) { Remove-Item $ExtractPath -Recurse -Force }
        Expand-Archive $ZipPath -DestinationPath $ExtractPath -Force

        # Find nssm.exe in extracted dir (win64 preferred, win32 fallback)
        $NssmBin = Get-ChildItem -Path $ExtractPath -Filter "nssm.exe" -Recurse |
            Where-Object { $_.FullName -like "*win64*" } | Select-Object -First 1
        if (-not $NssmBin) {
            $NssmBin = Get-ChildItem -Path $ExtractPath -Filter "nssm.exe" -Recurse | Select-Object -First 1
        }
        if (-not $NssmBin) { throw "nssm.exe not found in archive" }
        Copy-Item $NssmBin.FullName $NssmExe -Force
        Write-Host "NSSM installed: $NssmExe" -ForegroundColor Green
    } catch {
        Write-Error "Failed to download/extract NSSM: $_"
        Write-Host "Manual fix: Download from https://nssm.cc/download and put nssm.exe in $NssmDir"
        exit 1
    }
} else {
    Write-Host "NSSM already present: $NssmExe"
}

# ── Helper: install or reconfigure a service ──────────────────────────────────
function Install-JarvisService {
    param(
        [string]$Name,
        [string]$DisplayName,
        [string]$Exe,
        [string]$Args,
        [string]$StdoutLog,
        [string]$StderrLog,
        [string]$DependsOn = ""
    )

    Write-Host "Configuring service: $Name" -ForegroundColor Yellow

    # Remove if exists
    $existing = Get-Service $Name -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "  Removing existing $Name..."
        & $NssmExe stop $Name 2>$null
        & $NssmExe remove $Name confirm 2>$null
        Start-Sleep -Seconds 2
    }

    & $NssmExe install $Name $Exe $Args
    & $NssmExe set $Name AppDirectory $ProjectDir
    & $NssmExe set $Name DisplayName $DisplayName
    & $NssmExe set $Name Description "Jarvis AI Assistant — $DisplayName"
    & $NssmExe set $Name AppStdout $StdoutLog
    & $NssmExe set $Name AppStderr $StderrLog
    & $NssmExe set $Name AppRotateFiles 1
    & $NssmExe set $Name AppRotateBytes 10485760  # 10 MB
    & $NssmExe set $Name Start SERVICE_AUTO_START
    & $NssmExe set $Name AppExit Default Restart
    & $NssmExe set $Name AppRestartDelay 5000  # 5 sec
    & $NssmExe set $Name AppThrottle 10000      # min 10 sec between restarts

    if ($DependsOn) {
        & $NssmExe set $Name DependOnService $DependsOn
    }

    Write-Host "  $Name configured OK" -ForegroundColor Green
}

# ── Install services ───────────────────────────────────────────────────────────
Install-JarvisService `
    -Name "JarvisBackend" `
    -DisplayName "Jarvis Backend (uvicorn)" `
    -Exe $PythonExe `
    -Args "-m uvicorn app.main:app --host 127.0.0.1 --port 8010" `
    -StdoutLog "$StateDir\backend.log" `
    -StderrLog "$StateDir\backend.err.log"

Install-JarvisService `
    -Name "JarvisBot" `
    -DisplayName "Jarvis Telegram Bot" `
    -Exe $PythonExe `
    -Args "tools\jarvis_smart_telegram_control.py" `
    -StdoutLog "$StateDir\bot.log" `
    -StderrLog "$StateDir\bot.err.log" `
    -DependsOn "JarvisBackend"

Install-JarvisService `
    -Name "JarvisWatchdog" `
    -DisplayName "Jarvis Watchdog" `
    -Exe $PythonExe `
    -Args "app\services\system_watchdog.py" `
    -StdoutLog "$StateDir\watchdog.log" `
    -StderrLog "$StateDir\watchdog.err.log" `
    -DependsOn "JarvisBackend"

# ── Start services ─────────────────────────────────────────────────────────────
if (-not $NoStart) {
    Write-Host "`nStarting services..." -ForegroundColor Yellow

    Start-Service JarvisBackend
    Write-Host "JarvisBackend started, waiting 8s..."
    Start-Sleep -Seconds 8

    Start-Service JarvisBot
    Write-Host "JarvisBot started, waiting 3s..."
    Start-Sleep -Seconds 3

    Start-Service JarvisWatchdog
    Write-Host "JarvisWatchdog started"

    Start-Sleep -Seconds 2
    Write-Host "`n=== Service Status ===" -ForegroundColor Cyan
    Get-Service JarvisBackend, JarvisBot, JarvisWatchdog | Format-Table Name, Status, StartType
}

Write-Host "`n=== Installation Complete ===" -ForegroundColor Green
Write-Host "Logs: $StateDir\*.log"
Write-Host ""
Write-Host "Management commands:"
Write-Host "  Get-Service Jarvis*                    # status"
Write-Host "  Restart-Service JarvisBot              # restart bot"
Write-Host "  Get-Content $StateDir\bot.log -Tail 50"
Write-Host "  .\scripts\uninstall_service.ps1        # remove all"
