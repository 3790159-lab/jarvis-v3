# Jarvis Cleanup Audit Pack
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$OutDir = Join-Path "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram" "jarvis_stage3_artifacts\self_evolution\cleanup_audits"
if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$Out = Join-Path $OutDir "cleanup_$Stamp.json"
$Resp = Invoke-RestMethod -Method GET -Uri "http://127.0.0.1:8024/api/jarvis/n8n/lifecycle/cleanup-report?older_than_hours=0&max_keep_per_template=3&limit=200" -TimeoutSec 30
$Resp | ConvertTo-Json -Depth 80 | Set-Content -Encoding UTF8 -Path $Out
$Resp | ConvertTo-Json -Depth 80
