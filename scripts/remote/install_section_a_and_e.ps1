# scripts/remote/install_section_a_and_e.ps1
# Runs Sections A (OpenSSH Server) and E (cloudflared service) from
# docs/REMOTE_ACCESS.md in one go. Requires admin elevation — the script
# self-elevates via UAC if started without it.
#
# Usage:
#   Right-click → "Run with PowerShell"   (will prompt UAC)
#   OR
#   PowerShell -ExecutionPolicy Bypass -File C:\jarvis\scripts\remote\install_section_a_and_e.ps1

$ErrorActionPreference = "Stop"

# --- self-elevate ---
$current = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $current.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Not elevated — relaunching as administrator (UAC prompt incoming)…" -ForegroundColor Yellow
    $argList = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", "`"$PSCommandPath`""
    )
    Start-Process -FilePath "powershell.exe" -ArgumentList $argList -Verb RunAs
    return
}

Write-Host "=== Running as Administrator ===" -ForegroundColor Green
Write-Host ""

# --- Section A: OpenSSH Server ---
Write-Host "[A.1] Installing OpenSSH Server capability…" -ForegroundColor Cyan
try {
    Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 | Out-Null
    Write-Host "      OK" -ForegroundColor Green
} catch {
    Write-Host "      ERROR: $_" -ForegroundColor Red
}

Write-Host "[A.2] Starting sshd service…" -ForegroundColor Cyan
try {
    Start-Service sshd
    Write-Host "      OK" -ForegroundColor Green
} catch {
    Write-Host "      ERROR: $_" -ForegroundColor Red
}

Write-Host "[A.3] Setting sshd StartupType=Automatic…" -ForegroundColor Cyan
try {
    Set-Service -Name sshd -StartupType Automatic
    Write-Host "      OK" -ForegroundColor Green
} catch {
    Write-Host "      ERROR: $_" -ForegroundColor Red
}

Write-Host "[A.4] Creating firewall rule for TCP 22…" -ForegroundColor Cyan
$existing = Get-NetFirewallRule -DisplayName "OpenSSH Server (sshd)" -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "      Already exists — skipped" -ForegroundColor Yellow
} else {
    try {
        New-NetFirewallRule -DisplayName "OpenSSH Server (sshd)" `
            -Enabled True -Direction Inbound -Protocol TCP -Action Allow `
            -LocalPort 22 -Profile Any | Out-Null
        Write-Host "      OK" -ForegroundColor Green
    } catch {
        Write-Host "      ERROR: $_" -ForegroundColor Red
    }
}

# --- Section E: cloudflared service ---
$cfd = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
if (-not (Test-Path $cfd)) {
    Write-Host "[E] cloudflared.exe not found at $cfd — skipping Section E." -ForegroundColor Yellow
} else {
    Write-Host "[E.1] Installing cloudflared as Windows service…" -ForegroundColor Cyan
    $cfdSvc = Get-Service cloudflared -ErrorAction SilentlyContinue
    if ($cfdSvc) {
        Write-Host "      Already registered — skipped" -ForegroundColor Yellow
    } else {
        try {
            & $cfd service install
            Write-Host "      OK" -ForegroundColor Green
        } catch {
            Write-Host "      ERROR: $_" -ForegroundColor Red
        }
    }

    Write-Host "[E.2] Starting cloudflared service…" -ForegroundColor Cyan
    try {
        Start-Service cloudflared
        Write-Host "      OK" -ForegroundColor Green
    } catch {
        Write-Host "      ERROR: $_" -ForegroundColor Red
    }

    Write-Host "[E.3] Setting cloudflared StartupType=Automatic…" -ForegroundColor Cyan
    try {
        Set-Service cloudflared -StartupType Automatic
        Write-Host "      OK" -ForegroundColor Green
    } catch {
        Write-Host "      ERROR: $_" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "=== Final service states ===" -ForegroundColor Cyan
Get-Service sshd, cloudflared -ErrorAction SilentlyContinue |
    Format-Table Name, Status, StartType -AutoSize

Write-Host ""
Write-Host "When this window closes, run from a normal PowerShell:" -ForegroundColor Cyan
Write-Host "  C:\jarvis\scripts\remote\check_remote_status.ps1"
Write-Host ""
Read-Host "Press Enter to close"
