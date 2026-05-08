param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    [string]$HostName = "127.0.0.1",
    [int]$BackendPort = 8015
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Stop-ProcessOnPort {
    param([Parameter(Mandatory=$true)][int]$Port)

    try {
        $connections = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
        if ($connections) {
            $pids = $connections | Select-Object -ExpandProperty OwningProcess -Unique
            foreach ($pid in $pids) {
                if ($pid -and $pid -ne 0) {
                    try {
                        Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
                    } catch {
                    }
                }
            }
        }
    } catch {
    }
}

$PyExeCandidate = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$PyExe = if (Test-Path $PyExeCandidate) { $PyExeCandidate } else { "python" }

$LogsDir = Join-Path $ProjectRoot "jarvis_stage3_artifacts\logs"
if (-not (Test-Path $LogsDir)) {
    New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null
}

$StdOutLog = Join-Path $LogsDir "backend_n8n_stdout.log"
$StdErrLog = Join-Path $LogsDir "backend_n8n_stderr.log"

Stop-ProcessOnPort -Port $BackendPort
Start-Sleep -Seconds 1

$command = @"
Set-Location -Path '$ProjectRoot'
`$env:PYTHONIOENCODING = 'utf-8'
& '$PyExe' -m uvicorn app.main:app --host $HostName --port $BackendPort
"@

Start-Process -FilePath "powershell.exe" `
    -ArgumentList @("-NoProfile","-ExecutionPolicy","Bypass","-Command",$command) `
    -RedirectStandardOutput $StdOutLog `
    -RedirectStandardError $StdErrLog `
    -WindowStyle Normal | Out-Null

Write-Host "Waiting for backend..." -ForegroundColor Yellow
$deadline = (Get-Date).AddSeconds(40)
$health = $null

while ((Get-Date) -lt $deadline) {
    try {
        $health = Invoke-RestMethod -Method GET -Uri "http://$HostName`:$BackendPort/health" -TimeoutSec 5
        break
    } catch {
        Start-Sleep -Seconds 2
    }
}

if (-not $health) {
    Write-Host "Backend did not become healthy in time." -ForegroundColor Red
    Write-Host "STDOUT: $StdOutLog" -ForegroundColor Yellow
    Write-Host "STDERR: $StdErrLog" -ForegroundColor Yellow
    exit 1
}

Write-Host ""
Write-Host "=== BACKEND HEALTH ===" -ForegroundColor Green
$health | ConvertTo-Json -Depth 10

try {
    Write-Host ""
    Write-Host "=== N8N MATERIALIZER HEALTH ===" -ForegroundColor Green
    $n8nHealth = Invoke-RestMethod -Method GET -Uri "http://$HostName`:$BackendPort/api/n8n/materializer/health" -TimeoutSec 10
    $n8nHealth | ConvertTo-Json -Depth 10
} catch {
    Write-Host "N8N materializer health check failed: $($_.Exception.Message)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Logs:" -ForegroundColor Cyan
Write-Host "  $StdOutLog"
Write-Host "  $StdErrLog"