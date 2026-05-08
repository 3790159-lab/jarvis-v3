param(
    [string]$BaseUrl = "http://127.0.0.1:8015"
)
$ErrorActionPreference = "Stop"

$missionId = "mission_resume_" + ([guid]::NewGuid().ToString("N").Substring(0,8))
$snapshotBody = @{
  mission_id = $missionId
  objective = "Test resume and recovery flow"
  steps = @(
    @{ step_id="step1"; title="Step 1"; description="Prepare"; task_type="reasoning"; preferred_provider="ollama"; status="pending"; metadata=@{phase="prepare"} },
    @{ step_id="step2"; title="Step 2"; description="Execute"; task_type="coding"; preferred_provider="ollama"; status="pending"; metadata=@{phase="execute"} }
  )
} | ConvertTo-Json -Depth 20

Write-Host "== Create snapshot ==" -ForegroundColor Cyan
$snap = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/autonomy/missions/snapshot" -ContentType "application/json" -Body $snapshotBody
$snap | ConvertTo-Json -Depth 20

Write-Host "== Resume health ==" -ForegroundColor Cyan
$health = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/autonomy/resume/health"
$health | ConvertTo-Json -Depth 20

Write-Host "== List snapshots ==" -ForegroundColor Cyan
$shots = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/autonomy/resume/snapshots"
$shots | ConvertTo-Json -Depth 20

Write-Host "== Resume mission ==" -ForegroundColor Cyan
$resume = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/autonomy/missions/$missionId/resume"
$resume | ConvertTo-Json -Depth 20

Write-Host "== Recover mission ==" -ForegroundColor Cyan
$recover = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/autonomy/missions/$missionId/recover"
$recover | ConvertTo-Json -Depth 20

Write-Host "== Stale list ==" -ForegroundColor Cyan
$stale = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/autonomy/resume/stale?max_age_seconds=1"
$stale | ConvertTo-Json -Depth 20

Write-Host "Block 6.4 smoke test finished." -ForegroundColor Green
