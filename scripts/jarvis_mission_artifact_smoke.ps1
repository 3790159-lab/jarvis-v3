param(
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

$ErrorActionPreference = "Stop"

Write-Host "== Mission artifact health =="
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/mission-artifacts/health" | ConvertTo-Json -Depth 20

Write-Host "`n== Classify site objective =="
$classifySite = @{
    objective = "Create a landing page for Jarvis Studio with a hero section and CTA"
    constraints = @{
        title = "Jarvis Studio"
        hero = "Autonomous systems that create real outputs"
        subtitle = "Mission-integrated artifact generation flow"
        cta = "Launch Project"
    }
} | ConvertTo-Json -Depth 20

Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/mission-artifacts/classify" -ContentType "application/json" -Body $classifySite | ConvertTo-Json -Depth 50

Write-Host "`n== Execute site mission artifact =="
$execSite = @{
    objective = "Create a landing page for Jarvis Studio with a hero section and CTA"
    constraints = @{
        title = "Jarvis Studio"
        hero = "Autonomous systems that create real outputs"
        subtitle = "Mission-integrated artifact generation flow"
        cta = "Launch Project"
    }
} | ConvertTo-Json -Depth 20

Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/mission-artifacts/execute" -ContentType "application/json" -Body $execSite | ConvertTo-Json -Depth 50

Write-Host "`n== Execute table mission artifact =="
$execTable = @{
    objective = "Create a YouTube content calendar table for 30 days"
    constraints = @{
        name = "YouTube 30 Day Calendar"
    }
    preferred_task_type = "table"
    payload = @{
        rows = @(
            @{ day = 1; title = "Channel intro"; format = "short"; status = "planned" }
            @{ day = 2; title = "Problem / solution"; format = "short"; status = "planned" }
            @{ day = 3; title = "Tool breakdown"; format = "short"; status = "planned" }
        )
    }
} | ConvertTo-Json -Depth 20

Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/mission-artifacts/execute" -ContentType "application/json" -Body $execTable | ConvertTo-Json -Depth 50

Write-Host "`n== Execute game mission artifact =="
$execGame = @{
    objective = "Build a simple browser clicker game called Jarvis Tap"
    constraints = @{
        title = "Jarvis Tap"
    }
} | ConvertTo-Json -Depth 20

Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/mission-artifacts/execute" -ContentType "application/json" -Body $execGame | ConvertTo-Json -Depth 50

Write-Host "`n== Execute YouTube mission artifact =="
$execYoutube = @{
    objective = "Prepare a YouTube starter pack for an AI productivity channel"
    constraints = @{
        name = "AI Productivity Launch Pack"
        niche = "AI productivity"
        audience = "beginners and creators"
        tone = "practical and motivating"
    }
} | ConvertTo-Json -Depth 20

Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/mission-artifacts/execute" -ContentType "application/json" -Body $execYoutube | ConvertTo-Json -Depth 50

Write-Host "`nMission artifact smoke completed successfully."
