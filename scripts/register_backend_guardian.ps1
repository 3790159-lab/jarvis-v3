# Registers (or re-registers) the JarvisBackendGuardian scheduled task.
# Mirrors JarvisSniperDetached: S4U + RunLevel Highest so it runs whether the
# user is logged on or not (session-independent), MultipleInstances=IgnoreNew
# so the task itself never double-starts. Triggers: at system startup, at logon,
# AND a periodic 5-min repetition so the backend comes back after a reboot AND
# after the guardian process itself is killed (e.g. an OOM crunch during a
# regress -- incident 2026-07-09 02:25: guardian PID died mid-restart and the
# AtStartup/AtLogOn-only triggers plus 3x restart-on-failure gave up, leaving a
# wedged backend for ~10h). Because MultipleInstances=IgnoreNew, the 5-min
# repeat is a no-op while the guardian is alive and only relaunches it once
# dead. Registration does NOT start it -- run
# `schtasks /Run /TN JarvisBackendGuardian` (or reboot) to launch.

$ErrorActionPreference = 'Stop'
$TaskName = 'JarvisBackendGuardian'
$Script   = 'C:\jarvis\scripts\backend_guardian_detached.ps1'

$psArgs = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $Script
$Action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $psArgs -WorkingDirectory 'C:\jarvis'

# Workgroup (non-domain) machine: qualify the local account with the computer
# name so the SID maps (USERDOMAIN would wrongly be "WORKGROUP" here).
$Account = "$env:COMPUTERNAME\$env:USERNAME"

$Triggers = @(
    New-ScheduledTaskTrigger -AtStartup
    New-ScheduledTaskTrigger -AtLogOn -User $Account
    # Self-heal-the-healer: relaunch the guardian if its own process ever dies
    # (OOM, kill) and restart-on-failure has given up. IgnoreNew makes this a
    # no-op while the guardian is still running. ~10y duration = de-facto
    # indefinite ([TimeSpan]::MaxValue throws "out of range" here).
    New-ScheduledTaskTrigger -Once -At (Get-Date) `
        -RepetitionInterval (New-TimeSpan -Minutes 5) `
        -RepetitionDuration (New-TimeSpan -Days 3650)
)

# S4U: run whether logged on or not, without storing a password (same as sniper).
$Principal = New-ScheduledTaskPrincipal -UserId $Account `
    -LogonType S4U -RunLevel Highest

$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero)
$Settings.DisallowStartIfOnBatteries = $false
$Settings.StopIfGoingOnBatteries     = $false

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Triggers `
    -Principal $Principal -Settings $Settings -Force | Out-Null

Write-Host "[OK] Registered scheduled task '$TaskName' (S4U / Highest, AtStartup + AtLogOn)"
