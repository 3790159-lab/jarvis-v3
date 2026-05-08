param(
    [string]$BundleRoot = ".\jarvis_stage3_artifacts\phase_11_15_bundle"
)

Write-Host ""
Write-Host "=== Jarvis V3 Corrected Phase 11-15 Check ===" -ForegroundColor Cyan

$required = @(
    "docs\phase_11_15_roadmap.txt",
    "docs\source_of_truth.txt",
    "state\phase11_execution_orchestrator.json",
    "state\phase12_tool_hardening.json",
    "state\phase13_feedback_loop.json",
    "state\phase14_mission_memory_v2.json",
    "state\phase15_hitl_dashboard.json"
)

$allOk = $true

foreach ($rel in $required) {
    $path = Join-Path $BundleRoot $rel
    if (Test-Path $path) {
        Write-Host "[OK] $path" -ForegroundColor Green
    } else {
        Write-Host "[MISS] $path" -ForegroundColor Red
        $allOk = $false
    }
}

Write-Host ""
Write-Host "=== JSON Validation ===" -ForegroundColor Cyan

Get-ChildItem -Path (Join-Path $BundleRoot "state") -Filter "*.json" | ForEach-Object {
    try {
        Get-Content $_.FullName -Raw | ConvertFrom-Json | Out-Null
        Write-Host "[VALID] $($_.Name)" -ForegroundColor Green
    } catch {
        Write-Host "[INVALID] $($_.Name): $($_.Exception.Message)" -ForegroundColor Red
        $allOk = $false
    }
}

Write-Host ""
if ($allOk) {
    Write-Host "Bundle status: READY" -ForegroundColor Green
    exit 0
} else {
    Write-Host "Bundle status: NEEDS ATTENTION" -ForegroundColor Yellow
    exit 1
}
