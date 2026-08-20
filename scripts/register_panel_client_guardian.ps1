# Registers (or re-registers) the JarvisPanelClientGuardian scheduled task.
# Mirrors register_backend_guardian.ps1: S4U + RunLevel Highest so it runs
# whether the user is logged on or not (session-independent),
# MultipleInstances=IgnoreNew so the task itself never double-starts. Triggers:
# at system startup AND at logon, so the client panel comes back after a reboot
# -- the exact hole that killed it twice (20.08: the panel died with the
# interactive session that started it, no traceback, port 8011 free).
#
# ONE TASK, ONE CLIENT. Slug and port are baked into the task arguments on
# purpose: an unregistered task for a "future" client looks like supervision
# without being any. A second client gets its own task, registered the same way.
#
# Registration does NOT start the task -- run
# `schtasks /Run /TN JarvisPanelClientGuardian` (or reboot) to launch it.

$ErrorActionPreference = 'Stop'
$TaskName = 'JarvisPanelClientGuardian'
$Slug     = 'yarina'
# Port 8011 is named in FOUR places, and python cannot share a constant with
# PowerShell. Naming them here so editing one forces you to find the rest:
#   1. scripts/run_panel_client.py DEFAULT_PORT          -- what the panel binds
#   2. scripts/panel_client_guardian_detached.ps1 -Port  -- what the guardian watches
#   3. scripts/ops_watchdog.py PANEL_CLIENT_PORT         -- where the probe knocks
#   4. $Port here                                        -- what goes into the task
$Port     = 8011
$Root     = 'C:\jarvis'
$Script   = Join-Path (Join-Path $Root 'scripts') 'panel_client_guardian_detached.ps1'

$psArgs = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" -Slug {1} -Port {2}' -f $Script, $Slug, $Port
$Action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $psArgs -WorkingDirectory $Root

# Workgroup (non-domain) machine: qualify the local account with the computer
# name so the SID maps (USERDOMAIN would wrongly be "WORKGROUP" here).
$Account = "$env:COMPUTERNAME\$env:USERNAME"

$Triggers = @(
    New-ScheduledTaskTrigger -AtStartup
    New-ScheduledTaskTrigger -AtLogOn -User $Account
)

# S4U: run whether logged on or not, without storing a password.
$Principal = New-ScheduledTaskPrincipal -UserId $Account `
    -LogonType S4U -RunLevel Highest

# ExecutionTimeLimit = 0 (TimeSpan::Zero): the guardian is an infinite loop, and
# any finite limit would have the scheduler kill the supervisor itself.
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero)
$Settings.DisallowStartIfOnBatteries = $false
$Settings.StopIfGoingOnBatteries     = $false

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Triggers `
    -Principal $Principal -Settings $Settings -Force | Out-Null

Write-Host "[OK] Registered scheduled task '$TaskName' (S4U / Highest, AtStartup + AtLogOn), slug=$Slug port=$Port"
Write-Host "     Not started. Launch with: schtasks /Run /TN $TaskName"
