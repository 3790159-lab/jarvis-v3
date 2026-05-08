param()

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$EnvFile = Join-Path $ProjectRoot ".env"
if (-not (Test-Path $EnvFile)) {
    Write-Host "[WARN] .env file not found."
    exit 0
}

Write-Host "=== .env key lines ==="
Get-Content $EnvFile -Encoding UTF8 | Where-Object {
    $_ -match "^(APP_PORT|APP_HOST|API_BASE_URL|TELEGRAM_|BACKEND_|SUPERVISOR_)="
}

Write-Host "----------------------------------------"
Write-Host "[INFO] Verify that bot API base URL points to the same backend port you start."
Write-Host "[INFO] Current backend launcher default port: 8015"
