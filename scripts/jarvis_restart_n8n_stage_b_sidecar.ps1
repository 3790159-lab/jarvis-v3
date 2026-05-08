param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    [string]$HostName = "127.0.0.1",
    [int]$Port = 8025
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
                    try { Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue } catch {}
                }
            }
        }
    } catch {}
}

function Get-DotEnvMap {
    param([Parameter(Mandatory=$true)][string]$Path)
    $map = @{}
    if (-not (Test-Path $Path)) { return $map }

    foreach ($raw in Get-Content -Path $Path -Encoding UTF8) {
        $line = $raw.Trim()
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        if ($line.StartsWith("#")) { continue }
        $idx = $line.IndexOf("=")
        if ($idx -lt 1) { continue }

        $key = $line.Substring(0, $idx).Trim()
        $value = $line.Substring($idx + 1).Trim().Trim('"').Trim("'")
        if (-not [string]::IsNullOrWhiteSpace($key)) {
            $map[$key] = $value
        }
    }

    return $map
}

function Escape-SingleQuoted {
    param([string]$Value)
    if ($null -eq $Value) { return "" }
    return $Value.Replace("'", "''")
}

$PyExeCandidate = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$PyExe = if (Test-Path $PyExeCandidate) { $PyExeCandidate } else { "python" }

$LogsDir = Join-Path $ProjectRoot "jarvis_stage3_artifacts\logs"
if (-not (Test-Path $LogsDir)) {
    New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null
}

$EnvPath = Join-Path $ProjectRoot ".env"
$EnvMap = Get-DotEnvMap -Path $EnvPath

$N8NBaseUrl = if ($env:N8N_BASE_URL) { $env:N8N_BASE_URL } elseif ($EnvMap.ContainsKey("N8N_BASE_URL")) { $EnvMap["N8N_BASE_URL"] } else { "https://daniliyc.app.n8n.cloud" }
$N8NWebhookBaseUrl = if ($env:N8N_WEBHOOK_BASE_URL) { $env:N8N_WEBHOOK_BASE_URL } elseif ($EnvMap.ContainsKey("N8N_WEBHOOK_BASE_URL")) { $EnvMap["N8N_WEBHOOK_BASE_URL"] } else { $N8NBaseUrl }
$N8NApiKey = if ($env:N8N_API_KEY) { $env:N8N_API_KEY } elseif ($EnvMap.ContainsKey("N8N_API_KEY")) { $EnvMap["N8N_API_KEY"] } else { "" }
$N8NTimeout = if ($env:N8N_TIMEOUT_SECONDS) { $env:N8N_TIMEOUT_SECONDS } elseif ($EnvMap.ContainsKey("N8N_TIMEOUT_SECONDS")) { $EnvMap["N8N_TIMEOUT_SECONDS"] } else { "45" }

$StdOutLog = Join-Path $LogsDir "n8n_stage_b_sidecar_stdout.log"
$StdErrLog = Join-Path $LogsDir "n8n_stage_b_sidecar_stderr.log"

Stop-ProcessOnPort -Port $Port
Start-Sleep -Seconds 1

if (Test-Path $StdOutLog) { Remove-Item $StdOutLog -Force -ErrorAction SilentlyContinue }
if (Test-Path $StdErrLog) { Remove-Item $StdErrLog -Force -ErrorAction SilentlyContinue }

$command = @"
Set-Location -Path '$ProjectRoot'
`$env:PYTHONIOENCODING = 'utf-8'
`$env:PYTHONUNBUFFERED = '1'
`$env:N8N_BASE_URL = '$(Escape-SingleQuoted $N8NBaseUrl)'
`$env:N8N_WEBHOOK_BASE_URL = '$(Escape-SingleQuoted $N8NWebhookBaseUrl)'
`$env:N8N_API_KEY = '$(Escape-SingleQuoted $N8NApiKey)'
`$env:N8N_TIMEOUT_SECONDS = '$(Escape-SingleQuoted $N8NTimeout)'
& '$PyExe' -X utf8 -m uvicorn app.n8n_stage_b_sidecar:app --host $HostName --port $Port
"@

Start-Process -FilePath "powershell.exe" `
    -ArgumentList @("-NoProfile","-ExecutionPolicy","Bypass","-Command",$command) `
    -RedirectStandardOutput $StdOutLog `
    -RedirectStandardError $StdErrLog `
    -WindowStyle Normal | Out-Null

Write-Host "Waiting for sidecar..." -ForegroundColor Yellow
$deadline = (Get-Date).AddSeconds(45)
$health = $null

while ((Get-Date) -lt $deadline) {
    try {
        $health = Invoke-RestMethod -Method GET -Uri "http://$HostName`:$Port/health" -TimeoutSec 5
        break
    } catch {
        Start-Sleep -Seconds 2
    }
}

if (-not $health) {
    Write-Host "Sidecar did not become healthy in time." -ForegroundColor Red
    Write-Host "STDOUT: $StdOutLog" -ForegroundColor Yellow
    Write-Host "STDERR: $StdErrLog" -ForegroundColor Yellow
    exit 1
}

Write-Host ""
Write-Host "=== SIDECAR HEALTH ===" -ForegroundColor Green
$health | ConvertTo-Json -Depth 20

Write-Host ""
Write-Host "Logs:" -ForegroundColor Cyan
Write-Host "  $StdOutLog"
Write-Host "  $StdErrLog"