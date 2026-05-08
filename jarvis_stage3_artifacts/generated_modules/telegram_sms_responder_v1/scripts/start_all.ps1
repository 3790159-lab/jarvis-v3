param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

& (Join-Path $ProjectRoot "jarvis_stage3_artifacts\generated_modules\telegram_sms_responder_v1\scripts\start_module.ps1") -ProjectRoot $ProjectRoot -Port 8110
& (Join-Path $ProjectRoot "jarvis_stage3_artifacts\generated_modules\telegram_sms_responder_v1\scripts\start_bot.ps1") -ProjectRoot $ProjectRoot