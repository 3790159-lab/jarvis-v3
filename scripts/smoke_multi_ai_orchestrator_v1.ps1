param(
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

$ErrorActionPreference = "Stop"

Write-Host "=== MULTI-AI HEALTH ===" -ForegroundColor Cyan
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/multi-ai/health" | ConvertTo-Json -Depth 20

Write-Host "`n=== TEST: CONTROL ===" -ForegroundColor Cyan
$Body = @{ message = "Джарвис, ты тут? Проверь кто ты."; mode = "control" } | ConvertTo-Json -Depth 20
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/multi-ai/respond" -ContentType "application/json" -Body $Body | ConvertTo-Json -Depth 20

Write-Host "`n=== TEST: CLAUDE-FIRST ENGINEER ===" -ForegroundColor Cyan
$Body = @{ message = "Проанализируй архитектуру Jarvis backend и предложи 5 улучшений надёжности."; mode = "engineer" } | ConvertTo-Json -Depth 20
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/multi-ai/respond" -ContentType "application/json" -Body $Body | ConvertTo-Json -Depth 20