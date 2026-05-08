param(
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

Write-Host "== AI Router Health ==" -ForegroundColor Cyan
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/ai/health" | ConvertTo-Json -Depth 10

Write-Host ""
Write-Host "== General Task ==" -ForegroundColor Cyan
$Body1 = @{
    prompt = "Say hello from Jarvis AI router and describe selected path briefly."
    task_type = "general"
} | ConvertTo-Json -Depth 10
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/ai/dispatch" -ContentType "application/json" -Body $Body1 | ConvertTo-Json -Depth 10

Write-Host ""
Write-Host "== Reasoning Task ==" -ForegroundColor Cyan
$Body2 = @{
    prompt = "Compare local and cloud LLM routing for an autonomous supervisor in 5 concise points."
    task_type = "reasoning"
} | ConvertTo-Json -Depth 10
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/ai/dispatch" -ContentType "application/json" -Body $Body2 | ConvertTo-Json -Depth 10

Write-Host ""
Write-Host "== Coding Task ==" -ForegroundColor Cyan
$Body3 = @{
    prompt = "Write a small Python function that retries an async task with exponential backoff."
    task_type = "coding"
} | ConvertTo-Json -Depth 10
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/ai/dispatch" -ContentType "application/json" -Body $Body3 | ConvertTo-Json -Depth 10
