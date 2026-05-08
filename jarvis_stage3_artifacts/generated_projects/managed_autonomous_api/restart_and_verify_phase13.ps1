param(
    [string]$BaseUrl = "http://127.0.0.1:8010"
)

Set-Location "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram\jarvis_stage3_artifacts\generated_projects\managed_autonomous_api"

Write-Host ""
Write-Host "== Restart and Verify Phase 13 ==" -ForegroundColor Cyan

powershell -ExecutionPolicy Bypass -File ".\stop_8010_force.ps1"
if ($LASTEXITCODE -ne 0) {
    throw "Failed to free port 8010"
}

powershell -ExecutionPolicy Bypass -File ".\start_api_8010_detached.ps1" -BindHost "127.0.0.1" -BindPort 8010
if ($LASTEXITCODE -ne 0) {
    throw "Failed to start detached API process"
}

powershell -ExecutionPolicy Bypass -File ".\wait_api_8010.ps1" -BaseUrl $BaseUrl -TimeoutSeconds 35
if ($LASTEXITCODE -eq 2) {
    throw "API process exited before becoming ready"
}
if ($LASTEXITCODE -ne 0) {
    throw "API did not become reachable after restart"
}

powershell -ExecutionPolicy Bypass -File ".\verify_feedback_live.ps1" -BaseUrl $BaseUrl
if ($LASTEXITCODE -eq 2) {
    throw "API is unreachable even after restart"
}
if ($LASTEXITCODE -ne 0) {
    throw "Feedback routes are still not loaded in live runtime"
}

Write-Host ""
Write-Host "Phase 13 live runtime is ready." -ForegroundColor Green
