param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    [int]$Port = 8015,
    [int]$WaitSeconds = 60
)

Set-ExecutionPolicy -Scope Process Bypass -Force
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Set-Location $ProjectRoot

$PyExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $PyExe)) { $PyExe = "python" }

$LogDir = Join-Path $ProjectRoot "jarvis_stage3_artifacts\backend_logs"
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$OutLog = Join-Path $LogDir "backend_out_$Stamp.log"
$ErrLog = Join-Path $LogDir "backend_err_$Stamp.log"

Write-Host "=== STOP BACKEND ON PORT $Port ===" -ForegroundColor Cyan
Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique |
    Where-Object { $_ -and $_ -ne $PID } |
    ForEach-Object {
        Write-Host "Stopping PID $_" -ForegroundColor Yellow
        Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
    }

Start-Sleep -Seconds 2

Write-Host ""
Write-Host "=== COMPILE app.main ===" -ForegroundColor Cyan
& $PyExe -m py_compile ".\app\main.py"
if ($LASTEXITCODE -ne 0) { throw "app/main.py compile failed" }

Write-Host ""
Write-Host "=== START BACKEND ===" -ForegroundColor Cyan
$Proc = Start-Process `
    -FilePath $PyExe `
    -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$Port") `
    -WorkingDirectory $ProjectRoot `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog `
    -PassThru

Write-Host "Backend PID: $($Proc.Id)" -ForegroundColor Green
Write-Host "OutLog: $OutLog"
Write-Host "ErrLog: $ErrLog"

$BaseUrl = "http://127.0.0.1:$Port"
$Deadline = (Get-Date).AddSeconds($WaitSeconds)
$Healthy = $false
$LastError = $null

Write-Host ""
Write-Host "=== WAIT FOR HEALTH ===" -ForegroundColor Cyan

while ((Get-Date) -lt $Deadline) {
    if ($Proc.HasExited) {
        Write-Host "Backend process exited early." -ForegroundColor Red
        if (Test-Path $ErrLog) {
            Write-Host "=== STDERR ===" -ForegroundColor Red
            Get-Content $ErrLog -Tail 80
        }
        throw "Backend exited before health became available"
    }

    try {
        $health = Invoke-RestMethod -Method GET -Uri "$BaseUrl/health" -TimeoutSec 5
        $Healthy = $true
        Write-Host "Health OK" -ForegroundColor Green
        $health | ConvertTo-Json -Depth 10
        break
    }
    catch {
        $LastError = $_.Exception.Message
        Start-Sleep -Seconds 2
    }
}

if (-not $Healthy) {
    Write-Host "Backend did not become healthy in $WaitSeconds seconds." -ForegroundColor Red
    Write-Host "Last error: $LastError" -ForegroundColor Yellow
    if (Test-Path $ErrLog) {
        Write-Host "=== STDERR ===" -ForegroundColor Red
        Get-Content $ErrLog -Tail 100
    }
    throw "Backend health timeout"
}

Write-Host ""
Write-Host "=== UNIFIED NIGHT API HEALTH ===" -ForegroundColor Cyan
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/unified-night/health" -TimeoutSec 20 | ConvertTo-Json -Depth 10

Write-Host ""
Write-Host "=== UNIFIED NIGHT API METRICS ===" -ForegroundColor Cyan
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/unified-night/metrics" -TimeoutSec 20 | ConvertTo-Json -Depth 10

Write-Host ""
Write-Host "BACKEND HARDENED RESTART OK" -ForegroundColor Green