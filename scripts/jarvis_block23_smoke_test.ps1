param(
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

Write-Host "== Auto tool selection test =="

$missionId = "mission_auto_" + ([guid]::NewGuid().ToString("N").Substring(0,8))

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
      }
    }
  )
} | ConvertTo-Json -Depth 30

$result = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/missions/multistep/execute" -ContentType "application/json" -Body $body

$result | ConvertTo-Json -Depth 30