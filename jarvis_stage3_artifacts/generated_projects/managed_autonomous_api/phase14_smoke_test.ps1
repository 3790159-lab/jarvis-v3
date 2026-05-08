param(
    [string]$BaseUrl = "http://127.0.0.1:8010"
)

Write-Host ""
Write-Host "== Phase 14 Hardened Smoke Test ==" -ForegroundColor Cyan

Write-Host "`n[0] verify phase14 routes are loaded live" -ForegroundColor Yellow
powershell -ExecutionPolicy Bypass -File ".\verify_phase14_live.ps1" -BaseUrl $BaseUrl
if ($LASTEXITCODE -eq 2) {
    throw "Live API is unreachable. Restart the API first."
}
if ($LASTEXITCODE -ne 0) {
    throw "Phase 14 routes are not loaded in live runtime."
}

Write-Host "`n[1] memory v2 health" -ForegroundColor Yellow
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/mission-memory-v2/health" | ConvertTo-Json -Depth 20

Write-Host "`n[2] record success for youtube_shorts" -ForegroundColor Yellow
$success1 = @{
    mission_type = "youtube_shorts"
    mission_id = "mission_youtube_001"
    plan_summary = "Generate short script -> TTS -> video -> upload"
    reusable_pattern = "script_tts_video_upload"
    notes = "Worked with simple 4-step pipeline"
} | ConvertTo-Json
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/mission-memory-v2/record-success" -ContentType "application/json" -Body $success1 | ConvertTo-Json -Depth 20

Write-Host "`n[3] record failure for youtube_shorts" -ForegroundColor Yellow
$failure1 = @{
    mission_type = "youtube_shorts"
    mission_id = "mission_youtube_002"
    failure_signature = "video_generation_timeout"
    failed_stage = "execute_stage"
    notes = "Video provider timed out"
} | ConvertTo-Json
Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/mission-memory-v2/record-failure" -ContentType "application/json" -Body $failure1 | ConvertTo-Json -Depth 20

Write-Host "`n[4] suggest for youtube_shorts" -ForegroundColor Yellow
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/mission-memory-v2/suggest/youtube_shorts" | ConvertTo-Json -Depth 20

Write-Host "`n[5] get youtube_shorts" -ForegroundColor Yellow
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/mission-memory-v2/types/youtube_shorts" | ConvertTo-Json -Depth 20

Write-Host "`n[6] list mission types" -ForegroundColor Yellow
Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/mission-memory-v2/types" | ConvertTo-Json -Depth 20
