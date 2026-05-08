param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    [string]$BaseUrl = "http://127.0.0.1:8015",
    [int]$Limit = 8
)

$ErrorActionPreference = "Continue"
Set-Location $ProjectRoot

$RunId = "ultra_v8_1_" + (Get-Date -Format "yyyyMMdd_HHmmss")
$OutDir = Join-Path $ProjectRoot "jarvis_stage3_artifacts\ultra_upgrade_v8_1\$RunId"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$Log = Join-Path $OutDir "ultra_upgrade.log"

function Log-Line {
    param([string]$Text)
    $Line = "[" + (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + "] " + $Text
    Add-Content -Path $Log -Value $Line -Encoding UTF8
    Write-Host $Line
}

Log-Line "=== JARVIS ULTRA UPGRADE V8.1 STARTED ==="
Log-Line "RunId: $RunId"

Log-Line "1/9 Queue Guard"
& ".\scripts\jarvis_queue_guard_v7_7.ps1" -ProjectRoot $ProjectRoot -Restore *>&1 |
    Tee-Object -FilePath (Join-Path $OutDir "01_queue_guard.log")

Log-Line "2/9 Self-Healing"
& ".\scripts\jarvis_self_healing_v7_8.ps1" -ProjectRoot $ProjectRoot *>&1 |
    Tee-Object -FilePath (Join-Path $OutDir "02_self_healing.log")

Log-Line "3/9 Evidence"
& ".\scripts\jarvis_real_action_evidence_v6_3.ps1" -ProjectRoot $ProjectRoot -BaseUrl $BaseUrl *>&1 |
    Tee-Object -FilePath (Join-Path $OutDir "03_evidence.log")

Log-Line "4/9 Gateway Smoke"
& ".\scripts\jarvis_gateway_smoke_v7_0.ps1" -ProjectRoot $ProjectRoot *>&1 |
    Tee-Object -FilePath (Join-Path $OutDir "04_gateway.log")

Log-Line "5/9 n8n Pipeline Creator"
& ".\scripts\jarvis_n8n_pipeline_creator_v7_5.ps1" -ProjectRoot $ProjectRoot *>&1 |
    Tee-Object -FilePath (Join-Path $OutDir "05_n8n_pipeline_creator.log")

Log-Line "6/9 FULL CREATOR"
& ".\scripts\jarvis_full_creator_v8_0.ps1" `
    -ProjectRoot $ProjectRoot `
    -Idea "Jarvis Ultra Operator System: auto pipeline building, self-healing backend, improved night mode and full creator loop." `
    -AddQueue *>&1 |
    Tee-Object -FilePath (Join-Path $OutDir "06_full_creator.log")

Log-Line "7/9 Auto Task Generator"
& ".\scripts\jarvis_auto_task_generator_v7_2.ps1" -ProjectRoot $ProjectRoot -MaxNew 8 *>&1 |
    Tee-Object -FilePath (Join-Path $OutDir "07_auto_task_generator.log")

Log-Line "8/9 Strategic Brain"
& ".\scripts\jarvis_strategic_brain_cycle_v6_4.ps1" -ProjectRoot $ProjectRoot -BaseUrl $BaseUrl *>&1 |
    Tee-Object -FilePath (Join-Path $OutDir "08_strategic_brain.log")

Log-Line "9/9 Execute Plans"
& ".\scripts\jarvis_gateway_plan_executor_v7_2.ps1" -ProjectRoot $ProjectRoot -Limit $Limit *>&1 |
    Tee-Object -FilePath (Join-Path $OutDir "09_gateway_executor.log")

Log-Line "=== JARVIS ULTRA UPGRADE V8.1 COMPLETE ==="
Log-Line "Logs: $OutDir"