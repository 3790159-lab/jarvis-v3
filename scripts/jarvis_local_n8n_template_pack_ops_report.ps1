param(
    [string]$Base = "http://127.0.0.1:8020",
    [string]$StackDir = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram\n8n_docker_strong",
    [string]$OutDir = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram\jarvis_stage3_artifacts\local_n8n_control"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Ensure-Directory {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
}

function Write-Utf8NoBom {
    param([string]$Path, [string]$Content)
    $parent = Split-Path -Path $Path -Parent
    if ($parent -and -not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $enc = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $enc)
}

function Safe-Json {
    param([string]$Uri)
    try {
        return @{ ok = $true; body = (Invoke-RestMethod -Method GET -Uri $Uri -TimeoutSec 60) }
    } catch {
        return @{ ok = $false; error = $_.Exception.Message }
    }
}

Ensure-Directory -Path $OutDir
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"

$WhoAmI   = Safe-Json "$Base/__whoami"
$Version  = Safe-Json "$Base/api/jarvis/n8n/version"
$Status   = Safe-Json "$Base/api/jarvis/n8n/status/all?limit=20"
$Summary  = Safe-Json "$Base/api/jarvis/n8n/workflows/summary?limit=20"
$Templates= Safe-Json "$Base/api/jarvis/n8n/templates"

Set-Location -Path $StackDir
$DockerPs   = docker compose ps
$MainLogs   = docker compose logs n8n-main --tail 80
$WorkerLogs = docker compose logs n8n-worker --tail 80
$BridgeLogs = docker compose logs jarvis-bridge --tail 80

$JsonReport = [ordered]@{
    timestamp = $Stamp
    whoami = $WhoAmI
    version = $Version
    status_all = $Status
    workflows_summary = $Summary
    templates = $Templates
}

$JsonPath = Join-Path $OutDir "jarvis_local_n8n_template_pack_ops_$Stamp.json"
$TxtPath  = Join-Path $OutDir "jarvis_local_n8n_template_pack_logs_$Stamp.txt"

Write-Utf8NoBom -Path $JsonPath -Content ($JsonReport | ConvertTo-Json -Depth 50)

$Txt = @"
=== DOCKER PS ===
$DockerPs

=== N8N MAIN LOGS ===
$MainLogs

=== N8N WORKER LOGS ===
$WorkerLogs

=== BRIDGE LOGS ===
$BridgeLogs
"@
Write-Utf8NoBom -Path $TxtPath -Content $Txt

Write-Host "Saved JSON report: $JsonPath" -ForegroundColor Cyan
Write-Host "Saved logs report: $TxtPath" -ForegroundColor Cyan