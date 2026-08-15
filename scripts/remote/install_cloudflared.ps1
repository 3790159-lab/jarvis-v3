# scripts/remote/install_cloudflared.ps1
# Downloads the latest cloudflared Windows AMD64 binary to
# C:\jarvis\scripts\remote\cloudflared.exe. Does NOT install it as a
# service — that step requires admin and lives in REMOTE_ACCESS.md
# Section E.
#
# Usage:  C:\jarvis\scripts\remote\install_cloudflared.ps1

$ErrorActionPreference = "Stop"

$systemPath = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
if (Test-Path $systemPath) {
    $ver = & $systemPath --version 2>&1 | Select-Object -First 1
    Write-Host "cloudflared already installed system-wide:" -ForegroundColor Cyan
    Write-Host "  $systemPath"
    Write-Host "  $ver"
    Write-Host "No download needed. Use the system path in REMOTE_ACCESS.md." -ForegroundColor Green
    return
}

$dest = "C:\jarvis\scripts\remote\cloudflared.exe"
$destDir = Split-Path -Parent $dest

if (-not (Test-Path $destDir)) {
    New-Item -ItemType Directory -Path $destDir -Force | Out-Null
}

$url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
Write-Host "Downloading cloudflared from:" -ForegroundColor Cyan
Write-Host "  $url"
Write-Host "to:" -ForegroundColor Cyan
Write-Host "  $dest"

Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing

if (-not (Test-Path $dest)) {
    Write-Host "Download failed: $dest does not exist." -ForegroundColor Red
    exit 1
}

$ver = & $dest --version 2>&1 | Select-Object -First 1
Write-Host ""
Write-Host "Installed: $dest" -ForegroundColor Green
Write-Host "Version  : $ver" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps (see docs/REMOTE_ACCESS.md):" -ForegroundColor Cyan
Write-Host "  C   - tunnel login        (browser)"
Write-Host "  D   - tunnel create + DNS"
Write-Host "  E   - service install     (admin)"
