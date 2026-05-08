$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram"
$EnvFile = Join-Path $ProjectRoot ".env"

if (-not (Test-Path $EnvFile)) {
    Write-Host ".env not found" -ForegroundColor Red
    exit 1
}

$envMap = @{}
Get-Content $EnvFile | ForEach-Object {
    $line = $_.Trim()
    if (-not $line) { return }
    if ($line.StartsWith("#")) { return }
    $parts = $line -split "=", 2
    if ($parts.Count -eq 2) {
        $envMap[$parts[0].Trim()] = $parts[1]
    }
}

$token = $envMap["TELEGRAM_BOT_TOKEN"]
$chatId = $envMap["TELEGRAM_ALLOWED_CHAT_ID"]
$appHost = $envMap["APP_HOST"]
$appPort = $envMap["APP_PORT"]

Write-Host "TELEGRAM_BOT_TOKEN present: $([bool]($token -and $token.Trim()))" -ForegroundColor Cyan
if ($token -and $token.Trim()) {
    Write-Host "TELEGRAM_BOT_TOKEN length: $($token.Trim().Length)" -ForegroundColor Cyan
}

Write-Host "TELEGRAM_ALLOWED_CHAT_ID present: $([bool]($chatId -and $chatId.Trim()))" -ForegroundColor Cyan
if ($chatId -and $chatId.Trim()) {
    Write-Host "TELEGRAM_ALLOWED_CHAT_ID value: $($chatId.Trim())" -ForegroundColor Cyan
}

Write-Host "APP_HOST: $appHost" -ForegroundColor Cyan
Write-Host "APP_PORT: $appPort" -ForegroundColor Cyan
