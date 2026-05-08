param(
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

Write-Host "== Obsidian export note ==" -ForegroundColor Cyan
$NoteBody = @{
    category = "architecture"
    title = "Jarvis Memory Architecture"
    content = "Jarvis now supports semantic memory plus Obsidian-ready markdown export."
    tags = @("jarvis","architecture","obsidian")
    metadata = @{ stage = "block5"; source = "smoke_test" }
    links = @("Mission mission_block4_demo")
} | ConvertTo-Json -Depth 20
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/obsidian/export-note" -ContentType "application/json" -Body $NoteBody | ConvertTo-Json -Depth 20

Write-Host ""
Write-Host "== Auto mission memory ==" -ForegroundColor Cyan
$MissionBody = @{
    mission_id = "mission_block5_demo"
    objective = "Verify Obsidian-ready mission memory automation"
    summary = "Jarvis wrote semantic memory files and Obsidian-ready notes successfully."
    lessons_learned = @(
        "Structured markdown memory improves long-term maintainability",
        "Mission summaries should be written automatically"
    )
    known_issues = @(
        "Cloud providers are still optional and not yet configured"
    )
    tags = @("jarvis","memory","obsidian","mission")
    metadata = @{ stage = "block5"; source = "smoke_test" }
} | ConvertTo-Json -Depth 20
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/obsidian/auto-mission-memory" -ContentType "application/json" -Body $MissionBody | ConvertTo-Json -Depth 20
