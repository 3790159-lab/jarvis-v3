param(
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

$ErrorActionPreference = "Stop"

Write-Host "== Runtime bridge health =="
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/runtime-bridge/health" | ConvertTo-Json -Depth 20

Write-Host "`n== Submit auto-execute site mission =="
$siteBody = @{
    objective = "Create a landing page for Jarvis Runtime Studio with a hero section and CTA"
    constraints = @{
        title = "Jarvis Runtime Studio"
        hero = "Runtime-linked autonomous artifact execution"
        subtitle = "This mission was submitted through runtime bridge"
        cta = "Open Runtime"
    }
    auto_execute = $true
} | ConvertTo-Json -Depth 20

$siteSubmit = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/runtime-bridge/submit" -ContentType "application/json" -Body $siteBody
$siteSubmit | ConvertTo-Json -Depth 80

Write-Host "`n== Read goal record =="
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/runtime-bridge/goals/$($siteSubmit.goal_id)" | ConvertTo-Json -Depth 80

Write-Host "`n== Read mission record =="
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/runtime-bridge/missions/$($siteSubmit.mission_id)" | ConvertTo-Json -Depth 80

Write-Host "`n== Submit planned-only YouTube mission =="
$ytPlanBody = @{
    objective = "Prepare a YouTube starter pack for an AI automation channel"
    constraints = @{
        name = "AI Automation Runtime Pack"
        niche = "AI automation"
        audience = "builders and beginners"
        tone = "clear and practical"
    }
    auto_execute = $false
} | ConvertTo-Json -Depth 20

$ytPlanSubmit = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/runtime-bridge/submit" -ContentType "application/json" -Body $ytPlanBody
$ytPlanSubmit | ConvertTo-Json -Depth 80

Write-Host "`n== Read planned mission record =="
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/runtime-bridge/missions/$($ytPlanSubmit.mission_id)" | ConvertTo-Json -Depth 80

Write-Host "`n== Latest runtime bridge runs =="
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/runtime-bridge/runs" | ConvertTo-Json -Depth 80

Write-Host "`nRuntime bridge smoke completed successfully."
