Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

param([switch]$AllowCoreApply)

if (-not $AllowCoreApply) {
    throw "Core live apply is disabled for package: supervisor_router_hardening. Review package docs and use guarded merge only after explicit enable."
}

Write-Host "Core live apply placeholder for supervisor_router_hardening" -ForegroundColor Yellow
Write-Host "Review package: C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram\jarvis_stage3_artifacts\guarded_merge_executor\packages\supervisor_router_hardening__se-supervisor_router_hardening-30293a08" -ForegroundColor Cyan
