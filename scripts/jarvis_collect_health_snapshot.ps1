# Jarvis Health Snapshot Pack
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$OutDir = Join-Path "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram" "jarvis_stage3_artifacts\self_evolution\health_snapshots"
if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$Out = Join-Path $OutDir "health_$Stamp.json"
$Result = [ordered]@{
    ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    supervisor = Invoke-RestMethod -Method GET -Uri "http://127.0.0.1:8015/health" -TimeoutSec 15
    director = Invoke-RestMethod -Method GET -Uri "http://127.0.0.1:8024/api/jarvis/n8n/status/all?limit=50" -TimeoutSec 30
    bridge = Invoke-RestMethod -Method GET -Uri "http://127.0.0.1:8030/health" -TimeoutSec 15
}
$Result | ConvertTo-Json -Depth 80 | Set-Content -Encoding UTF8 -Path $Out
$Result | ConvertTo-Json -Depth 80
