param(
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

$ErrorActionPreference = "Stop"

Write-Host "== Artifact health =="
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/artifacts/health" | ConvertTo-Json -Depth 20

Write-Host "`n== Build table artifact =="
$tableBody = @{
    task_type   = "table"
    name        = "YouTube Content Calendar"
    description = "30-day content planning starter"
    payload     = @{
        rows = @(
            @{ day = 1; title = "Channel intro"; format = "short"; status = "planned" }
            @{ day = 2; title = "Pain point video"; format = "short"; status = "planned" }
            @{ day = 3; title = "Tool review"; format = "short"; status = "planned" }
        )
    }
} | ConvertTo-Json -Depth 20

$tableResult = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/artifacts/build" -ContentType "application/json" -Body $tableBody
$tableResult | ConvertTo-Json -Depth 50

Write-Host "`n== Build site artifact =="
$siteBody = @{
    task_type   = "site"
    name        = "Jarvis Studio Landing"
    description = "Simple landing page"
    payload     = @{
        title    = "Jarvis Studio"
        hero     = "Autonomous systems that create real outputs"
        subtitle = "This site was generated inside the artifact sandbox."
        cta      = "Launch Project"
    }
} | ConvertTo-Json -Depth 20

$siteResult = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/artifacts/build" -ContentType "application/json" -Body $siteBody
$siteResult | ConvertTo-Json -Depth 50

Write-Host "`n== Build game artifact =="
$gameBody = @{
    task_type   = "game"
    name        = "Jarvis Clicker"
    description = "Simple browser clicker"
    payload     = @{
        title = "Jarvis Clicker"
    }
} | ConvertTo-Json -Depth 20

$gameResult = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/artifacts/build" -ContentType "application/json" -Body $gameBody
$gameResult | ConvertTo-Json -Depth 50

Write-Host "`n== Build youtube pack artifact =="
$ytBody = @{
    task_type   = "youtube_pack"
    name        = "AI Growth Channel Starter"
    description = "Starter pack for a YouTube channel"
    payload     = @{
        niche    = "AI productivity"
        audience = "beginners and creators"
        tone     = "practical and motivating"
    }
} | ConvertTo-Json -Depth 20

$ytResult = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/artifacts/build" -ContentType "application/json" -Body $ytBody
$ytResult | ConvertTo-Json -Depth 50

Write-Host "`nArtifact smoke completed successfully."
