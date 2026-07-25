# Setup Cloudflare Tunnel for Jarvis home server
# Provides public HTTPS access without opening router ports
#
# Prerequisites:
#   1. Free Cloudflare account (cloudflare.com)
#   2. A domain added to Cloudflare (even a free one works)
#   3. Run once interactively for login, then service installs automatically

param(
    [string]$TunnelName = "jarvis-home",
    [string]$Domain = "",            # e.g. "jarvis.yourdomain.com"
    [switch]$Uninstall,
    [switch]$Status
)

$ErrorActionPreference = "Stop"
$ProjectDir = (Resolve-Path "$PSScriptRoot\..").Path
$CloudflarDir = "$ProjectDir\cloudflared"
$CloudflarExe = "$CloudflarDir\cloudflared.exe"
$ConfigDir = "$env:USERPROFILE\.cloudflared"
$ConfigFile = "$ConfigDir\config.yml"

# ── Status check ──────────────────────────────────────────────────────────────
if ($Status) {
    Write-Host "=== Cloudflare Tunnel Status ===" -ForegroundColor Cyan
    $svc = Get-Service "Jarvis-cloudflared" -ErrorAction SilentlyContinue
    if ($svc) {
        Write-Host "Service: $($svc.Status)" -ForegroundColor $(if ($svc.Status -eq "Running") { "Green" } else { "Red" })
    } else {
        Write-Host "Service: not installed" -ForegroundColor Yellow
    }
    if (Test-Path $CloudflarExe) {
        & $CloudflarExe tunnel list 2>$null
    }
    exit 0
}

# ── Uninstall ─────────────────────────────────────────────────────────────────
if ($Uninstall) {
    Write-Host "Uninstalling Cloudflare Tunnel service..."
    $svc = Get-Service "Jarvis-cloudflared" -ErrorAction SilentlyContinue
    if ($svc) {
        Stop-Service "Jarvis-cloudflared" -Force -ErrorAction SilentlyContinue
        & $CloudflarExe service uninstall 2>$null
    }
    Write-Host "Cloudflared service removed."
    exit 0
}

Write-Host "=== Cloudflare Tunnel Setup for Jarvis ===" -ForegroundColor Cyan
Write-Host "This will make Jarvis accessible at https://$Domain (if domain specified)"
Write-Host ""

# ── Download cloudflared ──────────────────────────────────────────────────────
if (-not (Test-Path $CloudflarExe)) {
    Write-Host "Downloading cloudflared..." -ForegroundColor Yellow
    if (-not (Test-Path $CloudflarDir)) { New-Item -ItemType Directory -Path $CloudflarDir -Force | Out-Null }

    $Arch = if ([Environment]::Is64BitOperatingSystem) { "amd64" } else { "386" }
    $DownloadUrl = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-$Arch.exe"

    try {
        Invoke-WebRequest -Uri $DownloadUrl -OutFile $CloudflarExe -UseBasicParsing
        Write-Host "cloudflared downloaded: $CloudflarExe" -ForegroundColor Green
    } catch {
        Write-Error "Download failed: $_"
        Write-Host "Manual: download from https://github.com/cloudflare/cloudflared/releases"
        exit 1
    }
} else {
    Write-Host "cloudflared already present: $CloudflarExe"
}

# ── Authenticate (interactive, browser-based) ────────────────────────────────
$CertFile = "$ConfigDir\cert.pem"
if (-not (Test-Path $CertFile)) {
    Write-Host ""
    Write-Host "STEP 1: Authenticate with Cloudflare" -ForegroundColor Yellow
    Write-Host "A browser window will open. Log in to Cloudflare and authorize."
    Write-Host ""
    & $CloudflarExe login
    if (-not (Test-Path $CertFile)) {
        Write-Error "Authentication failed — cert.pem not found at $CertFile"
        exit 1
    }
    Write-Host "Authenticated successfully!" -ForegroundColor Green
} else {
    Write-Host "Already authenticated (cert.pem found)"
}

# ── Create tunnel ─────────────────────────────────────────────────────────────
$TunnelIdFile = "$ConfigDir\tunnel_id.txt"
if (-not (Test-Path $TunnelIdFile)) {
    Write-Host ""
    Write-Host "STEP 2: Creating tunnel '$TunnelName'..." -ForegroundColor Yellow
    $output = & $CloudflarExe tunnel create $TunnelName 2>&1
    Write-Host $output
    # Extract tunnel ID from output
    $TunnelId = ($output | Select-String -Pattern "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}").Matches[0].Value
    if ($TunnelId) {
        $TunnelId | Out-File $TunnelIdFile -Encoding utf8
        Write-Host "Tunnel ID: $TunnelId" -ForegroundColor Green
    }
} else {
    $TunnelId = (Get-Content $TunnelIdFile).Trim()
    Write-Host "Using existing tunnel: $TunnelId"
}

# ── Write config.yml ──────────────────────────────────────────────────────────
if (-not (Test-Path $ConfigDir)) { New-Item -ItemType Directory -Path $ConfigDir -Force | Out-Null }

$BackendPort = 8010
$ConfigContent = @"
tunnel: $TunnelId
credentials-file: $ConfigDir\$TunnelId.json

ingress:
  # Telegram webhook endpoint
  - hostname: $Domain
    path: /telegram/webhook
    service: http://127.0.0.1:$BackendPort

  # Jarvis API (all other routes)
  - hostname: $Domain
    service: http://127.0.0.1:$BackendPort

  # Catch-all required by cloudflared
  - service: http_status:404
"@

$ConfigContent | Out-File $ConfigFile -Encoding utf8
Write-Host "Config written: $ConfigFile" -ForegroundColor Green

# ── DNS routing ───────────────────────────────────────────────────────────────
if ($Domain) {
    Write-Host ""
    Write-Host "STEP 3: Setting up DNS routing for $Domain..." -ForegroundColor Yellow
    & $CloudflarExe tunnel route dns $TunnelName $Domain
    Write-Host "DNS route created." -ForegroundColor Green
}

# ── Install as Windows Service ────────────────────────────────────────────────
Write-Host ""
Write-Host "STEP 4: Installing cloudflared as Windows Service..." -ForegroundColor Yellow
& $CloudflarExe service install
Start-Service "cloudflared" -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "=== Setup Complete ===" -ForegroundColor Green
if ($Domain) {
    Write-Host "Your Jarvis is now accessible at:" -ForegroundColor Cyan
    Write-Host "  https://$Domain" -ForegroundColor White
    Write-Host "  https://$Domain/health"
    Write-Host "  https://$Domain/telegram/webhook  (for Telegram webhook)"
}
Write-Host ""
Write-Host "Service management:"
Write-Host "  Get-Service cloudflared"
Write-Host "  .\scripts\setup_cloudflare_tunnel.ps1 -Status"
Write-Host "  .\scripts\setup_cloudflare_tunnel.ps1 -Uninstall"
