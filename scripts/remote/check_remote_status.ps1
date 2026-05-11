# scripts/remote/check_remote_status.ps1
# Health check for the remote-access stack: OpenSSH Server, cloudflared,
# firewall rule, and tunnel processes. Read-only — does not change state.
#
# Usage:  C:\jarvis\scripts\remote\check_remote_status.ps1

$ErrorActionPreference = "Continue"

function Write-Header($text) {
    Write-Host ""
    Write-Host "=== $text ===" -ForegroundColor Cyan
}

function Write-Ok($text)    { Write-Host "  [ok]   $text" -ForegroundColor Green }
function Write-Miss($text)  { Write-Host "  [miss] $text" -ForegroundColor Yellow }
function Write-Warn($text)  { Write-Host "  [warn] $text" -ForegroundColor Yellow }
function Write-Bad($text)   { Write-Host "  [bad]  $text" -ForegroundColor Red }

# --- OpenSSH Server ---
Write-Header "OpenSSH Server (sshd)"
$sshd = Get-Service sshd -ErrorAction SilentlyContinue
if (-not $sshd) {
    Write-Miss "sshd service not installed. See REMOTE_ACCESS.md Section A."
} else {
    if ($sshd.Status -eq "Running") {
        Write-Ok "sshd Running (StartType=$($sshd.StartType))"
    } else {
        Write-Bad "sshd installed but Status=$($sshd.Status)"
    }
}

# --- Firewall rule for port 22 ---
Write-Header "Firewall rule for SSH (TCP 22)"
$rule = Get-NetFirewallRule -DisplayName "*ssh*" -ErrorAction SilentlyContinue | Where-Object { $_.Enabled -eq "True" }
if (-not $rule) {
    Write-Miss "No enabled firewall rule mentioning 'ssh'."
} else {
    foreach ($r in $rule) { Write-Ok "$($r.DisplayName) (Enabled, $($r.Direction))" }
}

# --- cloudflared binary ---
Write-Header "cloudflared binary"
$candidates = @(
    "C:\Program Files (x86)\cloudflared\cloudflared.exe",
    "C:\Program Files\cloudflared\cloudflared.exe",
    "C:\jarvis\scripts\remote\cloudflared.exe"
)
$found = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($found) {
    $ver = & $found --version 2>&1 | Select-Object -First 1
    Write-Ok "$found  -->  $ver"
} else {
    Write-Miss "cloudflared not found in known locations. Run install_cloudflared.ps1 or install via package manager."
}

# --- cloudflared service ---
Write-Header "cloudflared service"
$cfd = Get-Service cloudflared -ErrorAction SilentlyContinue
if (-not $cfd) {
    Write-Miss "cloudflared service not registered (Section E)."
} else {
    if ($cfd.Status -eq "Running") {
        Write-Ok "cloudflared service Running (StartType=$($cfd.StartType))"
    } else {
        Write-Bad "cloudflared service installed but Status=$($cfd.Status)"
    }
}

# --- cloudflared processes ---
Write-Header "Running cloudflared processes"
$procs = Get-Process -Name cloudflared -ErrorAction SilentlyContinue
if (-not $procs) {
    Write-Miss "No cloudflared processes running."
} else {
    foreach ($p in $procs) {
        Write-Ok ("PID={0,-6}  CPU={1,8:N2}s  Start={2}" -f $p.Id, $p.CPU, $p.StartTime)
    }
}

# --- cloudflared config ---
Write-Header "cloudflared config + tunnels"
$cfDir = "$env:USERPROFILE\.cloudflared"
if (-not (Test-Path $cfDir)) {
    Write-Miss "$cfDir does not exist. Run 'cloudflared tunnel login' (Section C)."
} else {
    $cfg = Join-Path $cfDir "config.yml"
    if (Test-Path $cfg) { Write-Ok "config.yml present" } else { Write-Miss "config.yml missing" }
    $cert = Join-Path $cfDir "cert.pem"
    if (Test-Path $cert) { Write-Ok "cert.pem present (login completed)" } else { Write-Miss "cert.pem missing (no login)" }
    $creds = Get-ChildItem $cfDir -Filter "*.json" -ErrorAction SilentlyContinue
    if ($creds) { foreach ($c in $creds) { Write-Ok "credentials: $($c.Name)" } } else { Write-Miss "no tunnel credentials JSON" }
}

# --- listening sockets ---
Write-Header "Listening sockets (22, 3389, 8010)"
foreach ($port in 22, 3389, 8010) {
    $ok = Test-NetConnection -ComputerName 127.0.0.1 -Port $port -InformationLevel Quiet -WarningAction SilentlyContinue
    if ($ok) { Write-Ok "127.0.0.1:$port reachable" } else { Write-Miss "127.0.0.1:$port not reachable" }
}

# --- identity ---
Write-Header "Identity"
Write-Host "  USERNAME    : $env:USERNAME"
Write-Host "  COMPUTERNAME: $env:COMPUTERNAME"
Write-Host ""
Write-Host "When ready, follow: docs/REMOTE_ACCESS.md" -ForegroundColor Cyan
