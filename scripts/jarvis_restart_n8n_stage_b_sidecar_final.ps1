param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    [string]$HostName = "127.0.0.1",
    [int]$Port = 8035,
    [string]$N8NBaseUrl = "https://daniliyc.app.n8n.cloud",
    [string]$N8NWebhookBaseUrl = "https://daniliyc.app.n8n.cloud",
    [string]$N8NApiKey = "",
    [string]$N8NTimeoutSeconds = "45"
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

$StdOutLog = Join-Path $LogsDir "n8n_stage_b_sidecar_final_stdout.log"
$StdErrLog = Join-Path $LogsDir "n8n_stage_b_sidecar_final_stderr.log"
$ConfigJsonPath = Join-Path $ProjectRoot "jarvis_stage3_artifacts\config\n8n_sidecar_runtime_config.json"

Stop-ProcessOnPort -Port $Port
Start-Sleep -Seconds 1

if (Test-Path $StdOutLog) { Remove-Item $StdOutLog -Force -ErrorAction SilentlyContinue }
if (Test-Path $StdErrLog) { Remove-Item $StdErrLog -Force -ErrorAction SilentlyContinue }

$command = @"
Set-Location -Path '$ProjectRoot'
`$env:PYTHONIOENCODING = 'utf-8'
`$env:PYTHONUNBUFFERED = '1'
`$env:JARVIS_N8N_CONFIG_JSON = '$(Escape-SingleQuoted $ConfigJsonPath)'
`$env:N8N_BASE_URL = '$(Escape-SingleQuoted $N8NBaseUrl)'
`$env:N8N_WEBHOOK_BASE_URL = '$(Escape-SingleQuoted $N8NWebhookBaseUrl)'
`$env:N8N_API_KEY = '$(Escape-SingleQuoted $N8NApiKey)'
`$env:N8N_TIMEOUT_SECONDS = '$(Escape-SingleQuoted $N8NTimeoutSeconds)'
& '$PyExe' -X utf8 -m uvicorn app.n8n_stage_b_sidecar_final:app --host $HostName --port $Port
"@

Start-Process -FilePath "powershell.exe" `
    -ArgumentList @("-NoProfile","-ExecutionPolicy","Bypass","-Command",$command) `
    -RedirectStandardOutput $StdOutLog `
    -RedirectStandardError $StdErrLog `
    -WindowStyle Normal | Out-Null

Write-Host "Waiting for final sidecar..." -ForegroundColor Yellow
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
    Write-Host "Final sidecar did not become healthy in time." -ForegroundColor Red
    Write-Host "STDOUT: $StdOutLog" -ForegroundColor Yellow
    Write-Host "STDERR: $StdErrLog" -ForegroundColor Yellow
    exit 1
}

Write-Host ""
Write-Host "=== FINAL SIDECAR HEALTH ===" -ForegroundColor Green
$health | ConvertTo-Json -Depth 20