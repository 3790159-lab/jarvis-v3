param(
    [string]$BaseUrl = "http://127.0.0.1:8010"
)

Write-Host ""
Write-Host "== Jarvis Routes Audit ==" -ForegroundColor Cyan

try {
    $routes = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/runtime/routes" -TimeoutSec 5
} catch {
    Write-Host "API unreachable at $BaseUrl" -ForegroundColor Red
    exit 2
}

$groups = @(
    "/api/execution",
    "/api/tools",
    "/api/feedback",
    "/api/mission-memory-v2",
    "/api/hitl",
    "/api/autonomous-decision",
    "/api/runtime"
)

$result = @{}
foreach ($g in $groups) {
    $matched = @($routes.items | Where-Object { $_.path -like "$g*" })
    $result[$g] = $matched
}

$result | ConvertTo-Json -Depth 20
exit 0
