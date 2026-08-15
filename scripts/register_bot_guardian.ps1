# Registers (or re-registers) the JarvisBotGuardian scheduled task.
# Mirrors JarvisBackendGuardian / JarvisSniperDetached: S4U + RunLevel Highest
# so it runs whether the user is logged on or not (session-independent), so the
# bot recovers on a HEADLESS reboot without an interactive desktop logon.
# MultipleInstances=IgnoreNew; triggers at system startup, at logon AND on a
# repeating timer.
# Registration does NOT start it -- run `schtasks /Run /TN JarvisBotGuardian`.
#
# WHY THE TIMER (measured 2026-08-05): this task had only AtStartup+AtLogOn,
# so a dead guardian stayed dead until a reboot or an interactive logon -- the
# very thing S4U was chosen to avoid. Facts that day: the bot process started
# 08-01 05:13 while its guardian last ran 07-31 15:02, i.e. the main bot was
# running unattended. Backend/chatter/ops guardians all carry a repeat.
#
# Interval is 5 minutes (backend level, not chatter's 10): the main bot is the
# owner's primary control channel, so halve the worst-case outage window. The
# repeat costs nothing while the guardian is alive -- the extra start is
# dropped by MultipleInstances=IgnoreNew, and the guardian itself holds a PID
# lock as a second line of defence.
#
# INDEFINITE = -RepetitionInterval WITHOUT -RepetitionDuration. Never pass
# [TimeSpan]::MaxValue: it serialises to P99999999DT23H59M59S, the XML
# validator rejects it (HRESULT 0x80041318), Register-ScheduledTask fails and
# the task silently keeps its OLD definition -- exactly how the 07-25 deploy
# left the ops watchdog hole open.

$ErrorActionPreference = 'Stop'
$TaskName = 'JarvisBotGuardian'
$Script   = 'C:\jarvis\scripts\bot_guardian_detached.ps1'

$psArgs = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $Script
$Action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $psArgs -WorkingDirectory 'C:\jarvis'

# Workgroup (non-domain) machine: qualify the local account with the computer
# name so the SID maps (USERDOMAIN would wrongly be "WORKGROUP" here).
$Account = "$env:COMPUTERNAME\$env:USERNAME"

# AtStartup/AtLogOn STAY: after a reboot the guardian must come up at once,
# the timer only covers "guardian died while the machine kept running".
$Repeat = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 5)

$Triggers = @(
    New-ScheduledTaskTrigger -AtStartup
    New-ScheduledTaskTrigger -AtLogOn -User $Account
    $Repeat
)

$Principal = New-ScheduledTaskPrincipal -UserId $Account `
    -LogonType S4U -RunLevel Highest

$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero)
$Settings.DisallowStartIfOnBatteries = $false
$Settings.StopIfGoingOnBatteries     = $false

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Triggers `
    -Principal $Principal -Settings $Settings -Force | Out-Null

# VERIFY BY FACT, not by exit code: a rejected trigger is visible only in the
# live XML. Without this check the next deploy would silently leave the hole
# open again (precedent: 07-25 ops watchdog).
$reg = Get-ScheduledTask -TaskName $TaskName
$rep = @($reg.Triggers | Where-Object { $_.Repetition.Interval })
if ($rep.Count -eq 0) {
    throw "[FAIL] Scheduler did not accept the repeating trigger for '$TaskName' - no self-recovery"
}

Write-Host "[OK] Registered scheduled task '$TaskName' (S4U / Highest, AtStartup + AtLogOn + repeat)"
Write-Host ("     Repetition: {0}, duration '{1}' (empty = indefinite)" -f $rep[0].Repetition.Interval, $rep[0].Repetition.Duration)
