param(
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

$ErrorActionPreference = "Stop"

Write-Host "== Artifact health =="
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/artifacts/health" | ConvertTo-Json -Depth 30

Write-Host "`n== Build upgraded table artifact =="
$tableBody = @{
    task_type   = "table"
    name        = "YouTube Content Calendar Pro"
    description = "Upgraded deliverable table"
    payload     = @{
        rows = @(
            @{ day = 1; title = "Channel intro"; format = "short"; platform = "youtube"; status = "planned"; owner = "Jarvis" }
            @{ day = 2; title = "Pain point video"; format = "short"; platform = "youtube"; status = "planned"; owner = "Jarvis" }
            @{ day = 3; title = "Tool review"; format = "short"; platform = "youtube"; status = "planned"; owner = "Jarvis" }
        )
    }
} | ConvertTo-Json -Depth 30

$tableResult = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/artifacts/build" -ContentType "application/json" -Body $tableBody
$tableResult | ConvertTo-Json -Depth 80

Write-Host "`n== Build upgraded site artifact =="
$siteBody = @{
    task_type   = "site"
    name        = "Jarvis Studio Web Bundle"
    description = "Richer site deliverable"
    payload     = @{
        title    = "Jarvis Studio"
        hero     = "Autonomous systems that create real outputs"
        subtitle = "Richer website bundle with multiple pages"
        cta      = "Launch Project"
    }
} | ConvertTo-Json -Depth 30

$siteResult = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/artifacts/build" -ContentType "application/json" -Body $siteBody
$siteResult | ConvertTo-Json -Depth 80

Write-Host "`n== Build upgraded game artifact =="
$gameBody = @{
    task_type   = "game"
    name        = "Jarvis Arcade Bundle"
    description = "Richer browser game bundle"
    payload     = @{
        title = "Jarvis Arcade"
    }
} | ConvertTo-Json -Depth 30

$gameResult = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/artifacts/build" -ContentType "application/json" -Body $gameBody
$gameResult | ConvertTo-Json -Depth 80

Write-Host "`n== Build upgraded YouTube artifact =="
$ytBody = @{
    task_type   = "youtube_pack"
    name        = "AI Creator Launch Pack"
    description = "Draft-ready channel bundle"
    payload     = @{
        niche    = "AI productivity"
        audience = "builders and creators"
        tone     = "practical and motivating"
    }
} | ConvertTo-Json -Depth 30

$ytResult = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/artifacts/build" -ContentType "application/json" -Body $ytBody
$ytResult | ConvertTo-Json -Depth 80

Write-Host "`nReal deliverables smoke completed successfully."
