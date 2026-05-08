param(
    [string]$ProjectRoot = "",
    [string]$BackendBaseUrl = "http://127.0.0.1:8015",
    [string]$PanelHost = "127.0.0.1",
    [int]$PanelPort = 8026,
    [switch]$OpenBrowser,
    [switch]$StopExistingOnPort,
    [switch]$RunSmoke,
    [int]$WaitSeconds = 25
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Quote-Arg {
    param([Parameter(Mandatory = $true)][string]$Value)
    return '"' + ($Value -replace '"', '\"') + '"'
}

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
}

$ScriptsDir = Join-Path $ProjectRoot "scripts"
$LogsDir    = Join-Path $ProjectRoot "jarvis_stage3_artifacts\panel_logs"
$ServerPath = Join-Path $ScriptsDir "jarvis_operator_panel_server_v3.py"
$SmokePath  = Join-Path $ScriptsDir "jarvis_operator_panel_smoke_v3.ps1"
$PyExe      = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PyExe)) { $PyExe = "python" }

New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null

if (-not (Test-Path $ServerPath)) {
    throw "Panel server v3 not found: $ServerPath"
}

$stamp     = Get-Date -Format "yyyyMMdd_HHmmss"
$stdoutLog = Join-Path $LogsDir ("panel_v3_stdout_" + $stamp + ".log")
$stderrLog = Join-Path $LogsDir ("panel_v3_stderr_" + $stamp + ".log")
$panelUrl  = "http://{0}:{1}" -f $PanelHost, $PanelPort

if ($StopExistingOnPort) {
    try {
        $existing = Get-NetTCPConnection -LocalPort $PanelPort -State Listen -ErrorAction Stop | Select-Object -First 1
        if ($existing -and $existing.OwningProcess) {
            Stop-Process -Id $existing.OwningProcess -Force -ErrorAction SilentlyContinue
            Start-Sleep -Milliseconds 800
        }
    }
    catch { }
}

& $PyExe -m py_compile $ServerPath
if ($LASTEXITCODE -ne 0) {
    throw "Python compile failed for panel server v3: $ServerPath"
}

$argLine = @(
    (Quote-Arg $ServerPath),
    "--project-root", (Quote-Arg $ProjectRoot),
    "--backend-base-url", (Quote-Arg $BackendBaseUrl),
    "--host", (Quote-Arg $PanelHost),
    "--port", [string]$PanelPort
) -join " "

$proc = Start-Process `
    -FilePath $PyExe `
    -WorkingDirectory $ProjectRoot `
    -ArgumentList $argLine `
    -PassThru `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog

if (-not $proc) {
    throw "Failed to start panel process."
}

$metaOk = $false
for ($i = 0; $i -lt $WaitSeconds; $i++) {
    Start-Sleep -Seconds 1
    try {
        $null = Invoke-RestMethod -Method GET -Uri ($panelUrl + "/api/meta") -TimeoutSec 3
        $metaOk = $true
        break
    }
    catch { }
    if ($proc.HasExited) { break }
}

Write-Host ""
Write-Host "Panel process id : $($proc.Id)" -ForegroundColor Cyan
Write-Host "Panel URL        : $panelUrl/" -ForegroundColor Cyan
Write-Host "stdout log       : $stdoutLog" -ForegroundColor Cyan
Write-Host "stderr log       : $stderrLog" -ForegroundColor Cyan

if (-not $metaOk) {
    Write-Warning "Panel v3 did not become ready."
    Write-Host ""
    Write-Host "=== STDOUT TAIL ===" -ForegroundColor Yellow
    if (Test-Path $stdoutLog) { Get-Content $stdoutLog -Tail 120 }
    Write-Host ""
    Write-Host "=== STDERR TAIL ===" -ForegroundColor Yellow
    if (Test-Path $stderrLog) { Get-Content $stderrLog -Tail 120 }
    if ($proc.HasExited) {
        Write-Warning "Panel process already exited."
    }
    throw "Panel v3 startup failed. See logs above."
}

Write-Host ""
Write-Host "Panel v3 is ready." -ForegroundColor Green

if ($OpenBrowser) {
    Start-Process ($panelUrl + "/")
}

if ($RunSmoke -and (Test-Path $SmokePath)) {
    Write-Host ""
    Write-Host "=== PANEL V3 SMOKE ===" -ForegroundColor Green
    & $SmokePath -PanelUrl $panelUrl
}