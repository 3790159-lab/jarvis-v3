param()

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Port = 8015
$HostName = "127.0.0.1"

Set-Location $ProjectRoot
$ErrorActionPreference = "Stop"

if (Test-Path ".\.venv\Scripts\python.exe") {
    $Python = (Resolve-Path ".\.venv\Scripts\python.exe").Path
} else {
    $PythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $PythonCmd) { throw "Python not found in .venv or PATH." }
    $Python = $PythonCmd.Source
}

$LogDir = Join-Path $ProjectRoot "logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

$StdOutLog = Join-Path $LogDir "jarvis_backend_stdout.log"
$StdErrLog = Join-Path $LogDir "jarvis_backend_stderr.log"

Write-Host "== STOP BACKEND ==" -ForegroundColor Yellow
$Connections = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
if ($Connections) {
    $Pids = $Connections | Select-Object -ExpandProperty OwningProcess -Unique | Where-Object { $_ -gt 0 }
    foreach ($ProcId in $Pids) {
        try {
            Stop-Process -Id $ProcId -Force -ErrorAction Stop
            Write-Host "Killed PID $ProcId on port $Port" -ForegroundColor Green
        } catch {
            Write-Host "Failed to kill PID $ProcId : $($_.Exception.Message)" -ForegroundColor Red
        }
    }
} else {
    Write-Host "No running process found on port $Port" -ForegroundColor DarkGray
}

Start-Sleep -Seconds 2

if (Test-Path $StdOutLog) { Remove-Item $StdOutLog -Force -ErrorAction SilentlyContinue }
if (Test-Path $StdErrLog) { Remove-Item $StdErrLog -Force -ErrorAction SilentlyContinue }

Write-Host "== START BACKEND ==" -ForegroundColor Yellow
$Proc = Start-Process `
    -FilePath $Python `
    -ArgumentList @("-m","uvicorn","app.main:app","--host",$HostName,"--port",$Port) `
    -WorkingDirectory $ProjectRoot `
    -RedirectStandardOutput $StdOutLog `
    -RedirectStandardError $StdErrLog `
    -PassThru

Write-Host "Started backend PID: $($Proc.Id)" -ForegroundColor Green

$HealthOk = $false
for ($i = 1; $i -le 15; $i++) {
    Start-Sleep -Seconds 2
    $ProcessAlive = Get-Process -Id $Proc.Id -ErrorAction SilentlyContinue
    if (-not $ProcessAlive) {
        Write-Host "Backend process exited before health became ready." -ForegroundColor Red
        break
    }
    try {
        $health = Invoke-RestMethod -Method GET -Uri "http://$HostName`:$Port/health" -TimeoutSec 3
        $health | ConvertTo-Json -Depth 10
        Write-Host "Backend restarted successfully." -ForegroundColor Green
        $HealthOk = $true
        break
    } catch {
        Write-Host "Waiting for backend... $i/15" -ForegroundColor DarkYellow
    }
}

if (-not $HealthOk) {
    if (Test-Path $StdErrLog) {
        Write-Host "--- STDERR ---" -ForegroundColor Yellow
        Get-Content $StdErrLog -Tail 120
    }
    if (Test-Path $StdOutLog) {
        Write-Host "--- STDOUT ---" -ForegroundColor Yellow
        Get-Content $StdOutLog -Tail 120
    }
    throw "Backend did not become healthy."
}
