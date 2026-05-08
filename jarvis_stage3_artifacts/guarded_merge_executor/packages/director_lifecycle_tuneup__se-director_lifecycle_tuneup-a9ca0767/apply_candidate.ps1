Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

param([switch]$AllowCoreApply)

if (-not $AllowCoreApply) {
    throw "Core live apply is disabled for package: director_lifecycle_tuneup. Review package docs and use guarded merge only after explicit enable."
}

Write-Host "Core live apply placeholder for director_lifecycle_tuneup" -ForegroundColor Yellow
Write-Host "Review package: C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram\jarvis_stage3_artifacts\guarded_merge_executor\packages\director_lifecycle_tuneup__se-director_lifecycle_tuneup-a9ca0767" -ForegroundColor Cyan
