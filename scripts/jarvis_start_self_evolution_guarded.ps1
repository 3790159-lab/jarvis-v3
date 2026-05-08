param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    [string]$HostName = "127.0.0.1",
    [int]$Port = 8028
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Stop-ProcessOnPort {
    param([Parameter(Mandatory = $true)][int]$Port)
    try {
        $Conns = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
        $Ids = @($Conns | Select-Object -ExpandProperty OwningProcess -Unique)
        foreach ($ProcId in $Ids) {
            if ($ProcId -and $ProcId -ne $PID) {
                try { & taskkill.exe /PID $ProcId /T /F | Out-Null } catch { }
                try { Stop-Process -Id $ProcId -Force -ErrorAction SilentlyContinue } catch { }
            }
        }
    } catch { }
}

function Wait-PortFree {
    param([Parameter(Mandatory = $true)][int]$Port, [int]$TimeoutSec = 30)
    $Deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $Deadline) {
        $Busy = $false
        try { $Busy = [bool](Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue) } catch { $Busy = $false }
        if (-not $Busy) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

$PyExeCandidate = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$PyExe = if (Test-Path $PyExeCandidate) { $PyExeCandidate } else { "python" }

$LogsDir = Join-Path $ProjectRoot "jarvis_stage3_artifacts\logs"
if (-not (Test-Path $LogsDir)) { New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null }

$StdOutLog = Join-Path $LogsDir "jarvis_self_evolution_guarded_stdout.log"
$StdErrLog = Join-Path $LogsDir "jarvis_self_evolution_guarded_stderr.log"

Stop-ProcessOnPort -Port $Port
if (-not (Wait-PortFree -Port $Port -TimeoutSec 30)) {
    throw "Port $Port is still busy."
}

if (Test-Path $StdOutLog) { Remove-Item $StdOutLog -Force -ErrorAction SilentlyContinue }
if (Test-Path $StdErrLog) { Remove-Item $StdErrLog -Force -ErrorAction SilentlyContinue }

$Command = @"
Set-Location -Path '$ProjectRoot'
`$env:PYTHONIOENCODING = 'utf-8'
`$env:PYTHONUNBUFFERED = '1'
& '$PyExe' -X utf8 -m uvicorn app.jarvis_self_evolution_guarded:app --host $HostName --port $Port
"@

Start-Process `
    -FilePath "powershell.exe" `
    -WorkingDirectory $ProjectRoot `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $Command) `
    -RedirectStandardOutput $StdOutLog `
    -RedirectStandardError $StdErrLog `
    -WindowStyle Normal | Out-Null

$BaseUrl = "http://$HostName`:$Port"
$Deadline = (Get-Date).AddSeconds(60)
$Health = $null
while ((Get-Date) -lt $Deadline) {
    try {
        $Health = Invoke-RestMethod -Method GET -Uri "$BaseUrl/health" -TimeoutSec 10
        if ($Health.status -eq "healthy") { break }
    } catch { }
    Start-Sleep -Seconds 1
}

if (-not $Health) {
    throw "Self evolution guarded service did not become healthy in time."
}

Write-Host ""
Write-Host "=== SELF EVOLUTION GUARDED LIVE HEALTH ===" -ForegroundColor Green
$Health | ConvertTo-Json -Depth 30