# Registers (or re-registers) the JarvisIgSchedulePublisher scheduled task.
# Mirrors JarvisMorningDigest: S4U + RunLevel Highest so it runs whether the
# user is logged on or not (session-independent, reboot-resistant). Unlike
# the daily digest, this one repeats every 5 minutes to drain the
# /ig_schedule queue (state/ig_scheduled_posts.json) close to its due time.
#
# NOTE: this script only REGISTERS the task — it does not run it. Run once to
# verify manually before relying on the trigger:
#   schtasks /Run /TN JarvisIgSchedulePublisher
#
# Not executed as part of this dev_task (worktree scope / red line: no live
# scheduled-task registration from an autonomous task run) — run manually
# after merge, same as JarvisMorningDigest was.

$ErrorActionPreference = 'Stop'
$TaskName = 'JarvisIgSchedulePublisher'
$Python   = 'C:\jarvis\.venv\Scripts\python.exe'
$Script   = 'C:\jarvis\scripts\ig_schedule_publisher.py'

$psArgs = '"{0}"' -f $Script
$Action = New-ScheduledTaskAction -Execute $Python -Argument $psArgs -WorkingDirectory 'C:\jarvis'

# Workgroup (non-domain) machine: qualify the local account with the computer name.
$Account = "$env:COMPUTERNAME\$env:USERNAME"

# Fire once shortly after registration, then repeat every 5 minutes,
# indefinitely (RepetitionDuration capped at 10 years as a practical "forever").
$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 5) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$Principal = New-ScheduledTaskPrincipal -UserId $Account `
    -LogonType S4U -RunLevel Highest

# A single run must not overlap another (publish is not reentrant-safe across
# concurrent processes touching the same queue file); skip a tick rather than
# stack up if a prior run is still going (network hiccup / IG rate-limit backoff).
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 2) `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 4)
$Settings.DisallowStartIfOnBatteries = $false
$Settings.StopIfGoingOnBatteries     = $false

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
    -Principal $Principal -Settings $Settings -Force | Out-Null

Write-Host "[OK] Registered scheduled task '$TaskName' (S4U / Highest, every 5 min)"
