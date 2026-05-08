param(
    [string]$BaseUrl = "http://127.0.0.1:8010"
)

Write-Host ""
Write-Host "== Live Phase 14 Route Verification ==" -ForegroundColor Cyan

try {
    Write-Host "`n[1] runtime routes" -ForegroundColor Yellow
    $routes = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/runtime/routes" -TimeoutSec 5
} catch {
    Write-Host "Live API is unreachable at $BaseUrl" -ForegroundColor Red
    exit 2
}

$phase14 = $routes.items | Where-Object { $_.path -like "/api/mission-memory-v2*" }

if (-not $phase14) {
    Write-Host "Phase 14 routes are NOT loaded in live runtime" -ForegroundColor Red
    exit 1
}

$phase14 | ConvertTo-Json -Depth 10
Write-Host ""
Write-Host "Phase 14 routes are loaded in live runtime" -ForegroundColor Green
exit 0
