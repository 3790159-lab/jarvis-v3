param(
    [string]$GatewayBaseUrl = "http://127.0.0.1:8016",
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

Ensure-Directory -Path $OutDir

$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$Body = @{
    name = "jarvis-control-smoke-$Stamp"
    webhook_path = "jarvis-control-smoke-$Stamp"
} | ConvertTo-Json -Depth 10

$Resp = Invoke-RestMethod `
    -Method POST `
    -Uri "$GatewayBaseUrl/api/jarvis/n8n/smoke/local-webhook" `
    -ContentType "application/json" `
    -Body $Body `
    -TimeoutSec 120

$Json = $Resp | ConvertTo-Json -Depth 40
$OutPath = Join-Path $OutDir "jarvis_local_n8n_smoke_$Stamp.json"
Write-Utf8NoBom -Path $OutPath -Content $Json

$Resp | ConvertTo-Json -Depth 40
Write-Host ""
Write-Host "Saved report: $OutPath" -ForegroundColor Cyan