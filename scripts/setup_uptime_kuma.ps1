# DEV-17: Uptime Kuma (Docker) - independent monitor for backend/bot/cloudflared/
# disk, alerting straight to Telegram. On 15.07 the backend died at 03:14 and
# nobody found out until morning: the only thing that could have noticed was
# the bot's own heartbeat loop, which shares fate with the process it would
# need to alert about. Kuma runs as its own long-lived Docker container -
# deliberately NOT another process on the same Windows host that can die
# alongside bot/backend/guardian scripts.
#
# This script is run by Daniil interactively (needs Docker Desktop running,
# and the Telegram bot token/chat id typed directly into Kuma's web UI - CC
# never sees or handles that value, same discipline as scripts/add_secret.ps1
# and scripts/remote/setup_tailscale_channel.ps1). CC prepares and verifies
# the compose file + this wrapper but does not bring the stack up itself.
# See docs/UPTIME_KUMA_SETUP.md for the monitor/alert checklist to configure
# once the container is up.
#
# Usage (Daniil):
#   scripts\setup_uptime_kuma.ps1            # docker compose up -d + status
#   scripts\setup_uptime_kuma.ps1 -Status    # read-only status check
#   scripts\setup_uptime_kuma.ps1 -Down      # docker compose down

param(
    [switch]$Status,
    [switch]$Down,
    # Test hook: define the functions below only - never call docker on its
    # own. Mirrors scripts/remote/setup_tailscale_channel.ps1.
    [switch]$NoAutoRun
)

$ErrorActionPreference = 'Stop'

$script:ComposeDir  = Join-Path $PSScriptRoot '..\infra\uptime-kuma'
$script:ComposeFile = Join-Path $script:ComposeDir 'docker-compose.yml'
$script:KumaUrl     = 'http://127.0.0.1:3001'

function Test-DockerAvailable {
    # Single choke point for the docker CLI presence check - shadowed in tests.
    return [bool](Get-Command docker -ErrorAction SilentlyContinue)
}

function Invoke-DockerCompose {
    # Single choke point for shelling out to docker compose - shadowed in tests.
    param([Parameter(Mandatory = $true)][string[]]$ArgList)
    & docker compose --project-directory $script:ComposeDir -f $script:ComposeFile @ArgList 2>&1
}

function Test-KumaHttp {
    # Single choke point for the Kuma HTTP reachability probe - shadowed in tests.
    try {
        $resp = Invoke-WebRequest -Uri $script:KumaUrl -TimeoutSec 5 -UseBasicParsing
        return ($resp.StatusCode -ge 200) -and ($resp.StatusCode -lt 300)
    } catch {
        return $false
    }
}

function Get-KumaStatus {
    # Combines the three choke points above into one status object - pure
    # given fakes for Test-DockerAvailable/Invoke-DockerCompose/Test-KumaHttp,
    # so this is unit-testable without a real docker/Kuma install.
    if (-not (Test-DockerAvailable)) {
        return [pscustomobject]@{ DockerAvailable = $false; ContainerUp = $false; HttpOk = $false }
    }
    $ps = Invoke-DockerCompose -ArgList @('ps', '--status', 'running', '--services')
    $containerUp = [bool]($ps -match 'uptime-kuma')
    $httpOk = $false
    if ($containerUp) { $httpOk = Test-KumaHttp }
    return [pscustomobject]@{ DockerAvailable = $true; ContainerUp = $containerUp; HttpOk = $httpOk }
}

if ($NoAutoRun) { return }

if ($Down) {
    if (-not (Test-DockerAvailable)) {
        Write-Host 'docker CLI not found - install/start Docker Desktop first.'
        return
    }
    Invoke-DockerCompose -ArgList @('down') | Write-Host
    return
}

if ($Status) {
    $s = Get-KumaStatus
    Write-Host ("Docker available: {0}" -f $s.DockerAvailable)
    Write-Host ("Container up:     {0}" -f $s.ContainerUp)
    Write-Host ("HTTP reachable:   {0} ({1})" -f $s.HttpOk, $script:KumaUrl)
    return
}

if (-not (Test-DockerAvailable)) {
    Write-Host 'docker CLI not found - install/start Docker Desktop, then re-run this script.'
    return
}
Invoke-DockerCompose -ArgList @('up', '-d') | Write-Host
Start-Sleep -Seconds 3
$s = Get-KumaStatus
Write-Host ("Container up:   {0}" -f $s.ContainerUp)
Write-Host ("HTTP reachable: {0} ({1}) - open in a browser to finish setup (see docs/UPTIME_KUMA_SETUP.md)" -f $s.HttpOk, $script:KumaUrl)
