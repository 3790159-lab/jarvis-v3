param(
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

$ErrorActionPreference = "Stop"

Write-Host "== Router health ==" -ForegroundColor Cyan
$health = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/tools/router/health"
$health | ConvertTo-Json -Depth 20

Write-Host "== Router plan preview ==" -ForegroundColor Cyan
$planBody = @{
  objective = "Create a file with test content"
  step = @{
    title = "Auto step"
    description = "create file automatically"
    task_type = "coding"
    metadata = @{
      phase = "execute"
      router_mode = "hybrid"
    }
  }
} | ConvertTo-Json -Depth 20
$plan = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/tools/router/plan" -ContentType "application/json" -Body $planBody
$plan | ConvertTo-Json -Depth 20

Write-Host "== Auto routed multistep ==" -ForegroundColor Cyan
$missionId = "mission_block24_" + ([guid]::NewGuid().ToString("N").Substring(0,8))
$body = @{
  mission_id = $missionId
  objective = "Create a file with test content"
  steps = @(
    @{
      step_id = "auto_step"
      title = "Auto step"
      description = "create file automatically"
      task_type = "coding"
      preferred_provider = "ollama"
      metadata = @{
        phase = "execute"
        router_mode = "hybrid"
      }
    }
  )
} | ConvertTo-Json -Depth 30
$result = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/missions/multistep/execute" -ContentType "application/json" -Body $body
$result | ConvertTo-Json -Depth 40

Write-Host "Block 2.4 smoke test finished." -ForegroundColor Green