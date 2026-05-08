Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram"
$OutDir = Join-Path $ProjectRoot ("jarvis_stage3_artifacts\guarded_merge_executor\backups\" + (Get-Date -Format "yyyyMMdd_HHmmss"))
New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
$Targets = @(
    "app/jarvis_local_n8n_director_pro_core.py",
)
foreach ($T in $Targets) {
    $Full = Join-Path $ProjectRoot $T
    if (Test-Path $Full) {
        $Safe = $T -replace "[\\/:*?""<>|]", "__"
        Copy-Item -Path $Full -Destination (Join-Path $OutDir $Safe) -Recurse -Force -ErrorAction SilentlyContinue
    }
}
Write-Host $OutDir -ForegroundColor Cyan
