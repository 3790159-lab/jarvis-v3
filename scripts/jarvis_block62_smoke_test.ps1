param(
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

Write-Host "== Provider routing health ==" -ForegroundColor Cyan
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/provider-routing/health" | ConvertTo-Json -Depth 20

Write-Host ""
Write-Host "== Routing decision for coding ==" -ForegroundColor Cyan
$Body1 = @{
    task_type = "coding"
} | ConvertTo-Json -Depth 10
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/provider-routing/decide" -ContentType "application/json" -Body $Body1 | ConvertTo-Json -Depth 20

Write-Host ""
Write-Host "== Routing decision for reasoning ==" -ForegroundColor Cyan
$Body2 = @{
    task_type = "reasoning"
} | ConvertTo-Json -Depth 10
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/provider-routing/decide" -ContentType "application/json" -Body $Body2 | ConvertTo-Json -Depth 20

Write-Host ""
Write-Host "== External executor dry run with smart routing ==" -ForegroundColor Cyan
$Body3 = @{
    title = "Plan coding execution for Jarvis with smart routing"
    objective = "Verify smart provider policy for external executor"
    description = "Use the best provider chain for coding."
    task_type = "coding"
    dry_run = $true
    metadata = @{
        stage = "block62"
        source = "smoke_test"
    }
} | ConvertTo-Json -Depth 20
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/external-executor/execute" -ContentType "application/json" -Body $Body3 | ConvertTo-Json -Depth 20
