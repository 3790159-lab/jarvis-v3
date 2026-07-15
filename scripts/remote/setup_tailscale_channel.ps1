# DEV-15: second independent remote-access channel, not dependent on
# cloudflared. On 2026-07-15 an ingress edit + Restart-Service on cloudflared
# took SSH and RDP down simultaneously (both routed through the same
# tunnel) - only /infra_restart via the bot recovered the machine. Tailscale
# (WireGuard mesh, own binary/service, own auth, DERP-relay fallback so no
# inbound port is ever required) gives a network path that does not share
# any process, config file, or account with cloudflared - see
# docs/REMOTE_ACCESS_SECOND_CHANNEL.md for the alternatives considered.
#
# This script is run by Daniil interactively (winget install + `tailscale up`
# needs a real browser login, like `cloudflared tunnel login`) - CC prepares
# and verifies it but does not execute the real install itself. See
# docs/REMOTE_ACCESS_SECOND_CHANNEL.md.
#
# Usage (Daniil):
#   scripts\remote\setup_tailscale_channel.ps1            # install + status
#   scripts\remote\setup_tailscale_channel.ps1 -Status     # read-only check
#   scripts\remote\setup_tailscale_channel.ps1 -Uninstall  # remove

param(
    [switch]$Status,
    [switch]$Uninstall,
    # Test hook: define the functions below only - never call winget/tailscale
    # or touch a real service on its own. Mirrors scripts/add_secret.ps1 and
    # scripts/start_cc.ps1.
    [switch]$NoAutoRun
)

$ErrorActionPreference = 'Stop'

$script:TailscaleServiceName = 'Tailscale'

function Get-TailscaleService {
    # Single choke point for the real Get-Service call - shadowed in tests.
    Get-Service -Name $script:TailscaleServiceName -ErrorAction SilentlyContinue
}

function Invoke-Tailscale {
    # Single choke point for shelling out to the tailscale CLI - shadowed in
    # tests so logic can be verified without a real Tailscale install.
    param([Parameter(Mandatory = $true)][string[]]$ArgList)
    & tailscale @ArgList 2>&1
}

function Invoke-Winget {
    # Single choke point for shelling out to winget - shadowed in tests.
    param([Parameter(Mandatory = $true)][string[]]$ArgList)
    & winget @ArgList 2>&1
}

function Test-TailscaleServiceHealth {
    # Pure classification of a Get-Service-shaped object (or $null) - safe to
    # unit test with a fake pscustomobject, no real service needed.
    param($Service)
    if (-not $Service) {
        return [pscustomobject]@{
            Installed = $false; Running = $false; AutoStart = $false
            Healthy   = $false; Reason = 'not installed'
        }
    }
    $running = $Service.Status -eq 'Running'
    $autoStart = $Service.StartType -eq 'Automatic'
    $healthy = $running -and $autoStart
    $reason = if ($healthy) {
        'ok'
    } elseif (-not $running) {
        "service status=$($Service.Status)"
    } else {
        "service StartType=$($Service.StartType) (not Automatic)"
    }
    return [pscustomobject]@{
        Installed = $true; Running = $running; AutoStart = $autoStart
        Healthy   = $healthy; Reason = $reason
    }
}

function Get-TailscaleConnectionStatus {
    # Pure parse of `tailscale status` output. Logged-out/needs-login states
    # are the only ones `tailscale status` reports distinctly; any other
    # output (peer list, own IP line, etc.) means an authenticated tailnet.
    param([Parameter(Mandatory = $true)][string]$StatusText)
    $loggedOut = ($StatusText -match 'Logged out') -or ($StatusText -match 'NeedsLogin')
    return [pscustomobject]@{ LoggedIn = -not $loggedOut; Raw = $StatusText }
}

function Get-SecondChannelStatus {
    # Composes service health + CLI login state into the one report used by
    # -Status and by the CLAUDE.md "check second channel before touching
    # cloudflared/network" rule. Skips the CLI call entirely when the service
    # isn't even installed - nothing to ask it.
    $svc = Get-TailscaleService
    $health = Test-TailscaleServiceHealth -Service $svc
    if (-not $health.Installed) {
        return [pscustomobject]@{
            Installed = $false; Running = $false; AutoStart = $false
            LoggedIn  = $false; Healthy = $false; Reason = $health.Reason
        }
    }
    if (-not $health.Healthy) {
        return [pscustomobject]@{
            Installed = $true; Running = $health.Running; AutoStart = $health.AutoStart
            LoggedIn  = $false; Healthy = $false; Reason = $health.Reason
        }
    }
    $statusText = (Invoke-Tailscale -ArgList @('status')) -join "`n"
    $conn = Get-TailscaleConnectionStatus -StatusText $statusText
    $healthy = $conn.LoggedIn
    $reason = if ($healthy) { 'ok' } else { 'not logged in - run: tailscale up' }
    return [pscustomobject]@{
        Installed = $true; Running = $health.Running; AutoStart = $health.AutoStart
        LoggedIn  = $conn.LoggedIn; Healthy = $healthy; Reason = $reason
    }
}

function Write-SecondChannelStatus {
    param([Parameter(Mandatory = $true)]$ChannelStatus)
    Write-Host ""
    Write-Host "=== Tailscale (second channel) ===" -ForegroundColor Cyan
    if (-not $ChannelStatus.Installed) {
        Write-Host "  [miss] Tailscale not installed. Run this script without -Status to install." -ForegroundColor Yellow
        return
    }
    $svcColor = if ($ChannelStatus.Running) { 'Green' } else { 'Red' }
    Write-Host "  service Running=$($ChannelStatus.Running) AutoStart=$($ChannelStatus.AutoStart)" -ForegroundColor $svcColor
    $loginColor = if ($ChannelStatus.LoggedIn) { 'Green' } else { 'Yellow' }
    Write-Host "  LoggedIn=$($ChannelStatus.LoggedIn)" -ForegroundColor $loginColor
    $overallColor = if ($ChannelStatus.Healthy) { 'Green' } else { 'Red' }
    Write-Host "  Healthy=$($ChannelStatus.Healthy) ($($ChannelStatus.Reason))" -ForegroundColor $overallColor
}

if (-not $NoAutoRun) {
    if ($Status) {
        Write-SecondChannelStatus -ChannelStatus (Get-SecondChannelStatus)
        exit 0
    }

    if ($Uninstall) {
        $svc = Get-TailscaleService
        if ($svc) {
            Stop-Service -Name $script:TailscaleServiceName -Force -ErrorAction SilentlyContinue
        }
        Invoke-Winget -ArgList @('uninstall', '--id', 'Tailscale.Tailscale', '-e') | Out-Null
        Write-Host "Tailscale uninstalled (if it was present)." -ForegroundColor Yellow
        exit 0
    }

    $svc = Get-TailscaleService
    if (-not $svc) {
        Write-Host "Installing Tailscale via winget..." -ForegroundColor Yellow
        Invoke-Winget -ArgList @('install', '--id', 'Tailscale.Tailscale', '-e', '--silent',
            '--accept-package-agreements', '--accept-source-agreements') | Out-Null
        $svc = Get-TailscaleService
    }
    if ($svc) {
        Set-Service -Name $script:TailscaleServiceName -StartupType Automatic -ErrorAction SilentlyContinue
        Start-Service -Name $script:TailscaleServiceName -ErrorAction SilentlyContinue
    }

    Write-Host ""
    Write-Host "If not already logged in, run:  tailscale up" -ForegroundColor Yellow
    Write-Host "(opens a browser for one-time auth - same pattern as 'cloudflared tunnel login')"
    Write-SecondChannelStatus -ChannelStatus (Get-SecondChannelStatus)
}
