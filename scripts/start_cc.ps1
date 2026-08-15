# DEV-1: persistent Claude Code terminal via WSL + tmux.
#
# Problem: an interactive `claude` session run directly in a PowerShell/SSH
# window dies the instant SSH drops (laptop sleep, wifi blip, RDP hiccup) -
# losing a long-running task's context.
#
# Fix: run `claude` inside a tmux session hosted in WSL (Ubuntu). The tmux
# SERVER lives inside the WSL2 VM and keeps running independent of any
# attached client, so killing the SSH/PowerShell window that was attached to
# it does NOT kill the tmux session or the `claude` process inside it -
# reconnect and re-run this script (or `wsl -d Ubuntu -- tmux attach -t cc`)
# to pick the same session back up.
#
# Usage (Daniil, from a normal interactive PowerShell/SSH prompt):
#   scripts\start_cc.ps1                 # attach to session "cc", creating it if missing
#   scripts\start_cc.ps1 -Session other  # named session, e.g. for a second parallel task
#   scripts\start_cc.ps1 -New            # kill+recreate the session (fresh claude process)
#   scripts\start_cc.ps1 -NoAttach       # create/reattach but don't take over the terminal
#
# Requires: WSL Ubuntu distro + tmux installed inside it (already true on this
# box - if tmux is missing, Start-Cc fails honestly with the apt install
# command instead of proceeding to a cryptic tmux-not-found error), and the
# Windows-global `claude` CLI reachable from WSL via the npm interop shim at
# .../npm/claude (confirmed working: `wsl -d Ubuntu -- claude
# --version`).

param(
    [string]$Distro  = 'Ubuntu',
    [string]$Session = 'cc',
    [string]$WorkDir = '/mnt/c/jarvis',
    [string]$Command = 'claude',
    [switch]$New,
    # Create/reattach the session but skip the interactive `tmux attach` step.
    # Real, usable flag (not just a test hook) - e.g. to warm up a session from
    # a non-interactive script.
    [switch]$NoAttach,
    # Test hook: define the functions below only - never shell out to wsl.exe
    # at all. Prod usage (Daniil running this interactively, or -NoAttach
    # automation) never passes this, so real behavior is unchanged. Mirrors
    # the -NoLoop convention in scripts/bot_guardian_detached.ps1.
    [switch]$NoAutoRun
)

$ErrorActionPreference = 'Stop'

function Invoke-Wsl {
    # Single choke point for every real wsl.exe call, so tests can shadow
    # just this function (after dot-sourcing) instead of touching a real WSL
    # instance. Returns wsl.exe's exit code; stdout/stderr are inherited.
    # NOTE: the parameter is named -ArgList, not -Args - PowerShell's automatic
    # $args variable collides with a declared -Args param and silently drops
    # the bound value (caught by test_creates_new_session_when_none_exists
    # during development).
    param(
        [Parameter(Mandatory = $true)][string]$Distro,
        [Parameter(Mandatory = $true)][string[]]$ArgList
    )
    & wsl.exe -d $Distro -- @ArgList
    return $LASTEXITCODE
}

function Test-CcTmuxAvailable {
    # Checks the tmux binary is actually present inside the WSL distro -
    # separate from Test-CcTmuxSession's has-session probe so a distro without
    # tmux installed produces one clear, actionable message instead of an
    # opaque wsl.exe passthrough error surfacing later from has-session/
    # new-session.
    param([string]$Distro)
    return (Invoke-Wsl -Distro $Distro -ArgList @('bash', '-lc', 'command -v tmux >/dev/null 2>&1')) -eq 0
}

function Test-CcTmuxSession {
    param([string]$Distro, [string]$Session)
    return (Invoke-Wsl -Distro $Distro -ArgList @('tmux', 'has-session', '-t', $Session)) -eq 0
}

function New-CcTmuxSession {
    # Runs $Command through `bash -lc` inside the new pane rather than passing
    # it as a raw tmux argv token: tmux's trailing new-session argument is
    # exec'd literally, so a multi-word $Command (or one with future flags,
    # e.g. "claude --resume") would otherwise be looked up as a single program
    # name containing spaces and fail to launch (caught by the WSL/tmux
    # integration test during development - session silently never appeared).
    param([string]$Distro, [string]$Session, [string]$WorkDir, [string]$Command)
    Invoke-Wsl -Distro $Distro -ArgList @('tmux', 'new-session', '-d', '-s', $Session, '-c', $WorkDir, 'bash', '-lc', $Command) | Out-Null
}

function Remove-CcTmuxSession {
    param([string]$Distro, [string]$Session)
    Invoke-Wsl -Distro $Distro -ArgList @('tmux', 'kill-session', '-t', $Session) | Out-Null
}

function Start-Cc {
    # Core decision logic: reattach to a live session if one exists, otherwise
    # create it; -New forces a kill+recreate. Returns $true once the session
    # is confirmed to exist (whether reused or freshly created).
    param(
        [string]$Distro,
        [string]$Session,
        [string]$WorkDir,
        [string]$Command,
        [switch]$New,
        [switch]$NoAttach
    )

    if (-not (Test-CcTmuxAvailable -Distro $Distro)) {
        throw "tmux не найден внутри WSL($Distro). Установи и повтори: wsl -d $Distro -- sudo apt-get update && wsl -d $Distro -- sudo apt-get install -y tmux"
    }

    $exists = Test-CcTmuxSession -Distro $Distro -Session $Session

    if ($New -and $exists) {
        Remove-CcTmuxSession -Distro $Distro -Session $Session
        $exists = $false
    }

    if (-not $exists) {
        New-CcTmuxSession -Distro $Distro -Session $Session -WorkDir $WorkDir -Command $Command
    }

    if (-not $NoAttach) {
        Invoke-Wsl -Distro $Distro -ArgList @('tmux', 'attach', '-t', $Session) | Out-Null
    }

    return $true
}

if (-not $NoAutoRun) {
    Start-Cc -Distro $Distro -Session $Session -WorkDir $WorkDir -Command $Command -New:$New -NoAttach:$NoAttach
}
