# Jarvis Backup Core State Pack
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$OutDir = Join-Path "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram" "jarvis_stage3_artifacts\self_evolution\backups\$(Get-Date -Format yyyyMMdd_HHmmss)"
New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
$Paths = @(
    (Join-Path "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram" ".env"),
    (Join-Path "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram" "jarvis_stage3_artifacts\logs"),
    (Join-Path "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram" "jarvis_stage3_artifacts\self_evolution"),
    (Join-Path "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram" "jarvis_stage3_artifacts\n8n_templates")
)
foreach ($P in $Paths) {
    if (Test-Path $P) {
        $Name = Split-Path $P -Leaf
        Copy-Item -Path $P -Destination (Join-Path $OutDir $Name) -Recurse -Force -ErrorAction SilentlyContinue
    }
}
Write-Host $OutDir -ForegroundColor Cyan
