param(
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

Write-Host "== External executor health ==" -ForegroundColor Cyan
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/external-executor/health" | ConvertTo-Json -Depth 20

Write-Host ""
Write-Host "== External executor dry run ==" -ForegroundColor Cyan
$Body = @{
    title = "Prepare cloud coding execution for Jarvis"
    objective = "Verify cloud executor preparation layer"
    description = "Plan external coding execution with fallback policy."
    task_type = "coding"
    dry_run = $true
    metadata = @{
        stage = "block6"
        source = "smoke_test"
    }
} | ConvertTo-Json -Depth 20

Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/external-executor/execute" -ContentType "application/json" -Body $Body | ConvertTo-Json -Depth 20
