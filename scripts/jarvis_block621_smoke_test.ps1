param(
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

Write-Host "== Provider routing health ==" -ForegroundColor Cyan
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/provider-routing/health" | ConvertTo-Json -Depth 20

Write-Host ""
Write-Host "== External executor dry run ==" -ForegroundColor Cyan
$Body = @{
    title = "Validate strict provider filtering"
    objective = "Ensure dry-run never selects unavailable providers"
    description = "Use provider policy result exactly."
    task_type = "coding"
    dry_run = $true
    metadata = @{
        stage = "block621"
        source = "smoke_test"
    }
} | ConvertTo-Json -Depth 20

Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/external-executor/execute" -ContentType "application/json" -Body $Body | ConvertTo-Json -Depth 20
