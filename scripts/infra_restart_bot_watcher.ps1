# Detached one-shot bot restart+confirm for /infra_restart's [bot] button
# (DEV-12). Spawned (never waited on) by the LIVE bot process itself, since
# this script's job is to kill that very process: Stop-Process on your own
# python.exe needs no elevation, but the caller cannot outlive its own death,
# so THIS script -- not the bot -- sends the final Telegram confirmation,
# reading the token straight from .env (same standalone-script pattern as
# scripts/boot_watch_check.py / scripts/regress_watch_check.py) once the
# fresh bot's heartbeat is confirmed fresh.

param(
    [string]$Root = 'C:\jarvis',
    [int]$HeartbeatWaitSec = 45
)
$ErrorActionPreference = 'Continue'
Set-Location $Root
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$py      = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }
$botFile = Join-Path $Root 'tools\jarvis_smart_telegram_control.py'
$logDir  = Join-Path $Root 'state\logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$bOut    = Join-Path $logDir 'bot_boot.stdout.log'
$bErr    = Join-Path $logDir 'bot_boot.stderr.log'
$hbFile  = Join-Path $Root 'state\bot_heartbeat.txt'
$envFile = Join-Path $Root '.env'
$adminChatId = '237616472'

function Send-InfraRestartNotice([bool]$ok) {
    try {
        $envText = Get-Content -LiteralPath $envFile -Raw -ErrorAction Stop
        if ($envText -notmatch '(?m)^\s*(?:TELEGRAM_BOT_TOKEN|BOT_TOKEN)\s*=\s*"?([^"\r\n]+)"?') { return }
        $token = $Matches[1].Trim()
        $text = if ($ok) {
            "✅ /infra_restart: bot перезапущен, heartbeat свежий."
        } else {
            "⚠️ /infra_restart: bot перезапущен, но heartbeat НЕ подтверждён за ${HeartbeatWaitSec}с — проверь вручную."
        }
        $body = @{ chat_id = $adminChatId; text = $text } | ConvertTo-Json
        Invoke-RestMethod -Uri "https://api.telegram.org/bot$token/sendMessage" `
            -Method Post -Body $body -ContentType 'application/json' -TimeoutSec 15 | Out-Null
    } catch {}
}

# Kill the live bot (cmdline-scoped, mirrors bot_guardian_detached.ps1's
# Get-BotProcesses -- kept standalone here so that guardian script itself
# stays untouched).
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*$Root*jarvis_smart_telegram_control*" } |
    ForEach-Object { & taskkill.exe /PID $_.ProcessId /T /F *> $null }

Start-Sleep -Seconds 1
Start-Process -FilePath $py -ArgumentList @($botFile) -WorkingDirectory $Root `
    -WindowStyle Hidden -RedirectStandardOutput $bOut -RedirectStandardError $bErr | Out-Null

$healthy = $false
for ($i = 0; $i -lt $HeartbeatWaitSec; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Path $hbFile) {
        try {
            $last = [int64]((Get-Content $hbFile -ErrorAction Stop | Select-Object -First 1).Trim())
            $now  = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
            if (($now - $last) -le 30) { $healthy = $true; break }
        } catch {}
    }
}

Send-InfraRestartNotice -ok $healthy
