# Register Telegram webhook URL with Bot API
# Run after tunnel is up to switch from long-polling to webhook mode
#
# Usage:
#   .\scripts\register_telegram_webhook.ps1 -WebhookUrl "https://jarvis.yourdomain.com"
#
# To remove webhook (back to long-polling):
#   .\scripts\register_telegram_webhook.ps1 -Remove

param(
    [string]$WebhookUrl = "",
    [switch]$Remove,
    [switch]$Info
)

$BotToken = $env:TELEGRAM_BOT_TOKEN
if (-not $BotToken) {
    # Try reading from .env
    $EnvFile = Join-Path (Resolve-Path "$PSScriptRoot\..").Path ".env"
    if (Test-Path $EnvFile) {
        $line = Get-Content $EnvFile | Where-Object { $_ -match "^TELEGRAM_BOT_TOKEN=" }
        if ($line) { $BotToken = ($line -split "=", 2)[1].Trim().Trim('"').Trim("'") }
    }
}

if (-not $BotToken) {
    Write-Error "TELEGRAM_BOT_TOKEN not set. Set env var or add to .env file."
    exit 1
}

$TgBase = "https://api.telegram.org/bot$BotToken"

function Invoke-TgApi {
    param([string]$Method, [hashtable]$Body = @{})
    try {
        if ($Body.Count -gt 0) {
            $resp = Invoke-WebRequest -Uri "$TgBase/$Method" -Method POST -Body ($Body | ConvertTo-Json) `
                -ContentType "application/json" -UseBasicParsing -TimeoutSec 15
        } else {
            $resp = Invoke-WebRequest -Uri "$TgBase/$Method" -UseBasicParsing -TimeoutSec 15
        }
        return $resp.Content | ConvertFrom-Json
    } catch {
        Write-Error "Telegram API error: $_"
        return $null
    }
}

# ── Info ──────────────────────────────────────────────────────────────────────
if ($Info) {
    Write-Host "=== Current Webhook Info ===" -ForegroundColor Cyan
    $result = Invoke-TgApi "getWebhookInfo"
    if ($result.ok) {
        $wi = $result.result
        Write-Host "URL:              $($wi.url)"
        Write-Host "Pending updates:  $($wi.pending_update_count)"
        Write-Host "Last error:       $($wi.last_error_message)"
        Write-Host "Max connections:  $($wi.max_connections)"
    } else {
        Write-Host "Failed to get webhook info" -ForegroundColor Red
    }
    exit 0
}

# ── Remove webhook ────────────────────────────────────────────────────────────
if ($Remove) {
    Write-Host "Removing webhook (switching back to long-polling)..." -ForegroundColor Yellow
    $result = Invoke-TgApi "deleteWebhook" @{ drop_pending_updates = $true }
    if ($result -and $result.ok) {
        Write-Host "Webhook removed. Bot will use long-polling on next start." -ForegroundColor Green
    } else {
        Write-Host "Failed to remove webhook: $($result | ConvertTo-Json)" -ForegroundColor Red
    }
    exit 0
}

# ── Set webhook ───────────────────────────────────────────────────────────────
if (-not $WebhookUrl) {
    Write-Error "Provide -WebhookUrl 'https://jarvis.yourdomain.com' or use -Remove/-Info"
    exit 1
}

$FullUrl = $WebhookUrl.TrimEnd("/") + "/telegram/webhook"
Write-Host "=== Setting Telegram Webhook ===" -ForegroundColor Cyan
Write-Host "URL: $FullUrl"
Write-Host ""

$result = Invoke-TgApi "setWebhook" @{
    url                  = $FullUrl
    max_connections      = 40
    drop_pending_updates = $false
    allowed_updates      = @("message", "callback_query", "inline_query")
}

if ($result -and $result.ok) {
    Write-Host "Webhook registered successfully!" -ForegroundColor Green
    Write-Host ""
    Write-Host "Next steps:" -ForegroundColor Yellow
    Write-Host "  1. Set WEBHOOK_URL=$WebhookUrl in your .env"
    Write-Host "  2. Restart bot: the reader thread will auto-activate"
    Write-Host "  3. Verify: .\scripts\register_telegram_webhook.ps1 -Info"
} else {
    Write-Host "Failed to set webhook:" -ForegroundColor Red
    Write-Host ($result | ConvertTo-Json)
    exit 1
}
