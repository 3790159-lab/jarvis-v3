param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    [Parameter(Mandatory = $true)][string]$TelegramBotToken,
    [Parameter(Mandatory = $true)][string]$TelegramAllowedChatId,
    [string]$SupervisorBaseUrl = "http://127.0.0.1:8015"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$EnvPath = Join-Path $ProjectRoot "jarvis_stage3_artifacts\generated_modules\telegram_sms_responder_v1\config\module.local.env"

$Content = @"
MODULE_NAME=telegram_sms_responder_v1
MODULE_PORT=8110
SUPERVISOR_BASE_URL=$SupervisorBaseUrl
SUPERVISOR_RESPONSE_PATH=/api/respond
TELEGRAM_BOT_TOKEN=$TelegramBotToken
TELEGRAM_ALLOWED_CHAT_ID=$TelegramAllowedChatId
TELEGRAM_POLL_TIMEOUT_SECONDS=25
TELEGRAM_SEND_CHUNK_LIMIT=3500
"@

$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($EnvPath, $Content, $Utf8NoBom)

Write-Host ""
Write-Host "=== TELEGRAM MODULE ENV WRITTEN ===" -ForegroundColor Green
Write-Host $EnvPath -ForegroundColor Cyan