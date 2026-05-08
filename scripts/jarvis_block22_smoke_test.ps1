param(
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

$ErrorActionPreference = "Stop"

Write-Host "== Test restricted shell ==" -ForegroundColor Cyan
$missionId1 = "mission_block22_shell_" + ([guid]::NewGuid().ToString("N").Substring(0,8))
$body1 = @{
  mission_id = $missionId1
  objective = "Test shell blocked in safe mode"
  steps = @(
    @{
      step_id = "blocked_shell"
      title = "Blocked shell"
      description = "Should fail in safe mode"
      task_type = "coding"
      preferred_provider = "ollama"
      metadata = @{
        phase = "execute"
        mode = "safe"
        tool = "shell"
        tool_payload = @{
          command = "Write-Output 'should_not_run'"
          timeout_seconds = 10
          working_directory = "."
        }
      }
    }
  )
} | ConvertTo-Json -Depth 30
$result1 = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/missions/multistep/execute" -ContentType "application/json" -Body $body1
$result1 | ConvertTo-Json -Depth 30

Write-Host "== Test allowed tools ==" -ForegroundColor Cyan
$missionId2 = "mission_block22_allow_" + ([guid]::NewGuid().ToString("N").Substring(0,8))
$body2 = @{
  mission_id = $missionId2
  objective = "Test allowed_tools restriction"
  steps = @(
    @{
      step_id = "blocked_write"
      title = "Blocked file write"
      description = "Should fail because file_write is not allowed"
      task_type = "coding"
      preferred_provider = "ollama"
      metadata = @{
        phase = "execute"
        allowed_tools = @("file_read")
        tool = "file_write"
        tool_payload = @{
          path = "jarvis_stage3_artifacts/tool_runtime/outputs/block22_forbidden.txt"
          content = "should not be written"
        }
      }
    }
  )
} | ConvertTo-Json -Depth 30
$result2 = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/missions/multistep/execute" -ContentType "application/json" -Body $body2
$result2 | ConvertTo-Json -Depth 30

Write-Host "== Test normal file write ==" -ForegroundColor Cyan
$missionId3 = "mission_block22_ok_" + ([guid]::NewGuid().ToString("N").Substring(0,8))
$body3 = @{
  mission_id = $missionId3
  objective = "Test normal file write"
  steps = @(
    @{
      step_id = "ok_write"
      title = "Allowed write"
      description = "Should succeed"
      task_type = "coding"
      preferred_provider = "ollama"
      metadata = @{
        phase = "execute"
        mode = "restricted"
        allowed_tools = @("file_write")
        tool = "file_write"
        tool_payload = @{
          path = "jarvis_stage3_artifacts/tool_runtime/outputs/block22_ok.txt"
          content = "block22_success"
        }
      }
    }
  )
} | ConvertTo-Json -Depth 30
$result3 = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/missions/multistep/execute" -ContentType "application/json" -Body $body3
$result3 | ConvertTo-Json -Depth 30

Write-Host "Block 2.2 smoke test finished." -ForegroundColor Green