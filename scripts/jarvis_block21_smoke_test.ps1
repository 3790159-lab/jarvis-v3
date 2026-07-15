param(
    [string]$BaseUrl = "http://127.0.0.1:8015"
)
$ErrorActionPreference = "Stop"

# Auth for the default-deny guard (audit 2026-07-15). Sends X-API-Key from env.
$ApiKey = $env:JARVIS_INTERNAL_API_KEY
if (-not $ApiKey) { $ApiKey = $env:JARVIS_ADMIN_KEY }
$AuthHeaders = @{}
if ($ApiKey) { $AuthHeaders["X-API-Key"] = $ApiKey }

Write-Host "== Tools health ==" -ForegroundColor Cyan
$health = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/tools/health" -Headers $AuthHeaders
$health | ConvertTo-Json -Depth 20

Write-Host "== Tools registry ==" -ForegroundColor Cyan
$registry = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/tools/registry" -Headers $AuthHeaders
$registry | ConvertTo-Json -Depth 20

Write-Host "== File write tool ==" -ForegroundColor Cyan
$writeBody = @{
  tool = "file_write"
  payload = @{
    path = "jarvis_stage3_artifacts/tool_runtime/outputs/block21_demo.txt"
    content = "hello from block 2.1"
  }
} | ConvertTo-Json -Depth 20
$writeResult = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/tools/execute" -Headers $AuthHeaders -ContentType "application/json" -Body $writeBody
$writeResult | ConvertTo-Json -Depth 20

Write-Host "== File read tool ==" -ForegroundColor Cyan
$readBody = @{
  tool = "file_read"
  payload = @{
    path = "jarvis_stage3_artifacts/tool_runtime/outputs/block21_demo.txt"
  }
} | ConvertTo-Json -Depth 20
$readResult = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/tools/execute" -Headers $AuthHeaders -ContentType "application/json" -Body $readBody
$readResult | ConvertTo-Json -Depth 20

Write-Host "== Python tool ==" -ForegroundColor Cyan
$pythonBody = @{
  tool = "python"
  payload = @{
    code = "print(""block21_python_ok"")"
    timeout_seconds = 15
  }
} | ConvertTo-Json -Depth 20
$pythonResult = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/tools/execute" -Headers $AuthHeaders -ContentType "application/json" -Body $pythonBody
$pythonResult | ConvertTo-Json -Depth 20

Write-Host "== Shell tool ==" -ForegroundColor Cyan
$shellBody = @{
  tool = "shell"
  payload = @{
    command = "Write-Output 'block21_shell_ok'"
    timeout_seconds = 15
    working_directory = "."
  }
} | ConvertTo-Json -Depth 20
$shellResult = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/tools/execute" -Headers $AuthHeaders -ContentType "application/json" -Body $shellBody
$shellResult | ConvertTo-Json -Depth 20

Write-Host "== Multistep with tool ==" -ForegroundColor Cyan
$missionId = "mission_tool_" + ([guid]::NewGuid().ToString("N").Substring(0, 8))
$missionBody = @{
  mission_id = $missionId
  objective = "Test tool-enabled multistep execution"
  steps = @(
    @{
      step_id = "write_artifact"
      title = "Write artifact"
      description = "Write an output artifact"
      task_type = "coding"
      preferred_provider = "ollama"
      metadata = @{
        phase = "execute"
        tool = "file_write"
        tool_payload = @{
          path = "jarvis_stage3_artifacts/tool_runtime/outputs/multistep_artifact.txt"
          content = "artifact from multistep tool execution"
        }
      }
    }
  )
} | ConvertTo-Json -Depth 30
$missionResult = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/missions/multistep/execute" -Headers $AuthHeaders -ContentType "application/json" -Body $missionBody
$missionResult | ConvertTo-Json -Depth 30

Write-Host "Block 2.1 smoke test finished." -ForegroundColor Green
