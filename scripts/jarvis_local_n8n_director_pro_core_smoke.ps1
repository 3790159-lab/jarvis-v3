param(
    [string]$Base = "http://127.0.0.1:8024",
    [string]$TemplateId = "webhook_inbox_v5",
    [string]$OutDir = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram\jarvis_stage3_artifacts\local_n8n_control",
    [switch]$ReuseExisting
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
    template_id = $TemplateId
    name = "$TemplateId-$Stamp"
    webhook_path = "$TemplateId-$Stamp"
    payload = @{
        source = "jarvis_local_n8n_director_pro_core_smoke"
        message = "hello from director pro core smoke"
        timestamp = $Stamp
    }
    reuse_existing = [bool]$ReuseExisting
    wait_seconds = 4
} | ConvertTo-Json -Depth 20

$Resp = Invoke-RestMethod `
    -Method POST `
    -Uri "$Base/api/jarvis/n8n/templates/smoke" `
    -ContentType "application/json" `
    -Body $Body `
    -TimeoutSec 180

$OutPath = Join-Path $OutDir "jarvis_local_n8n_director_pro_core_smoke_$Stamp.json"
$Json = $Resp | ConvertTo-Json -Depth 120
Write-Utf8NoBom -Path $OutPath -Content $Json

$Resp | ConvertTo-Json -Depth 120
Write-Host ""
Write-Host "Saved report: $OutPath" -ForegroundColor Cyan