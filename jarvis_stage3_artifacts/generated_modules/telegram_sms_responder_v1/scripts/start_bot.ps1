param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ModuleDir = Join-Path $ProjectRoot "jarvis_stage3_artifacts\generated_modules\telegram_sms_responder_v1"
$PyExeCandidate = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$PyExe = if (Test-Path $PyExeCandidate) { $PyExeCandidate } else { "python" }
$LogsDir = Join-Path $ProjectRoot "jarvis_stage3_artifacts\logs"
if (-not (Test-Path $LogsDir)) { New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null }

$EnvLocal = Join-Path $ModuleDir "config\module.local.env"
if (-not (Test-Path $EnvLocal)) {
    throw "module.local.env not found: $EnvLocal"
}
$Raw = Get-Content -Raw -Path $EnvLocal -Encoding UTF8
if ($Raw -match "PUT_REAL_BOT_TOKEN_HERE" -or $Raw -match "PUT_REAL_CHAT_ID_HERE") {
    throw "Fill TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_CHAT_ID in config\module.local.env before starting the bot."
}

$StdOutLog = Join-Path $LogsDir "telegram_sms_responder_v1_bot_stdout.log"
$StdErrLog = Join-Path $LogsDir "telegram_sms_responder_v1_bot_stderr.log"

if (Test-Path $StdOutLog) { Remove-Item $StdOutLog -Force -ErrorAction SilentlyContinue }
if (Test-Path $StdErrLog) { Remove-Item $StdErrLog -Force -ErrorAction SilentlyContinue }

$Command = @"
Set-Location -Path '$ModuleDir'
`$env:PYTHONIOENCODING = 'utf-8'
`$env:PYTHONUNBUFFERED = '1'
& '$PyExe' -X utf8 telegram_bot.py
"@

Start-Process `
    -FilePath "powershell.exe" `
    -WorkingDirectory $ModuleDir `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $Command) `
    -RedirectStandardOutput $StdOutLog `
    -RedirectStandardError $StdErrLog `
    -WindowStyle Normal | Out-Null

$global:LASTEXITCODE = 0
Write-Host ""
Write-Host "=== TELEGRAM MODULE BOT STARTED ===" -ForegroundColor Green
Write-Host $StdOutLog -ForegroundColor Cyan
Write-Host $StdErrLog -ForegroundColor Cyan
exit 0