# Jarvis Log Summary Pack
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$LogsDir = Join-Path "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram" "jarvis_stage3_artifacts\logs"
$OutDir = Join-Path "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram" "jarvis_stage3_artifacts\self_evolution\log_summaries"
if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$Out = Join-Path $OutDir "log_summary_$Stamp.json"
$Patterns = @("bind_error","Traceback","ERROR","Exception","warning","WARNING")
$Rows = @()
Get-ChildItem $LogsDir -File -ErrorAction SilentlyContinue | ForEach-Object {
    $Text = Get-Content $_.FullName -Tail 300 -ErrorAction SilentlyContinue
    $Counts = @{}
    foreach ($Pat in $Patterns) {
        $Counts[$Pat] = @($Text | Select-String -Pattern $Pat -SimpleMatch).Count
    }
    $Rows += [pscustomobject]@{ path = $_.FullName; counts = $Counts }
}
$Rows | ConvertTo-Json -Depth 40 | Set-Content -Encoding UTF8 -Path $Out
$Rows | ConvertTo-Json -Depth 40
