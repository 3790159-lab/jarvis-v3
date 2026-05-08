param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    [string]$BaseUrl = "http://127.0.0.1:8015",
    [int]$Limit = 5
)

$ErrorActionPreference = "Continue"
Set-Location $ProjectRoot

$OutDir = Join-Path $ProjectRoot "jarvis_stage3_artifacts\night_mode_tool_gateway_v7_0"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

function Log-Line {
    param([string]$Text)
    $Line = "[" + (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + "] " + $Text
    Add-Content -Path (Join-Path $OutDir "night_gateway.log") -Value $Line -Encoding UTF8
    Write-Host $Line
}

Log-Line "Night gateway cycle started."

Log-Line "1/4 Evidence gate"
& ".\scripts\jarvis_real_action_evidence_v6_3.ps1" -ProjectRoot $ProjectRoot -BaseUrl $BaseUrl

Log-Line "2/4 Tool gateway smoke"
& ".\scripts\jarvis_gateway_smoke_v7_0.ps1" -ProjectRoot $ProjectRoot

Log-Line "3/4 Strategic brain"
& ".\scripts\jarvis_strategic_brain_cycle_v6_4.ps1" -ProjectRoot $ProjectRoot -BaseUrl $BaseUrl

Log-Line "4/4 Real action runner"
& ".\scripts\jarvis_real_action_runner_v6_6.ps1" -ProjectRoot $ProjectRoot -Limit $Limit

Log-Line "Night gateway cycle completed."