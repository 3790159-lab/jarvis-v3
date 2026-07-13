# Registers (or re-registers) the JarvisMorningDigest scheduled task.
# Mirrors JarvisIgTokenRefresh: S4U + RunLevel Highest so it runs whether the
# user is logged on or not (session-independent, reboot-resistant).
# StartWhenAvailable catches up a run skipped while the PC was off.
#
# Registration does NOT run it now -- run once to verify:
#   schtasks /Run /TN JarvisMorningDigest
# or wait for the daily 09:00 trigger.

$ErrorActionPreference = 'Stop'
$TaskName = 'JarvisMorningDigest'
$Python   = 'C:\jarvis\.venv\Scripts\python.exe'
$Script   = 'C:\jarvis\scripts\morning_digest.py'

$psArgs = '"{0}"' -f $Script
$Action = New-ScheduledTaskAction -Execute $Python -Argument $psArgs -WorkingDirectory 'C:\jarvis'

# Workgroup (non-domain) machine: qualify the local account with the computer name.
$Account = "$env:COMPUTERNAME\$env:USERNAME"

# Daily at 09:00.
$Trigger = New-ScheduledTaskTrigger -Daily -At 9:00am

$Principal = New-ScheduledTaskPrincipal -UserId $Account `
    -LogonType S4U -RunLevel Highest

# Run a missed schedule as soon as possible; retry a few times on transient
# failure; a single run must not overlap another.
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 10) `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
$Settings.DisallowStartIfOnBatteries = $false
$Settings.StopIfGoingOnBatteries     = $false

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
    -Principal $Principal -Settings $Settings -Force | Out-Null

Write-Host "[OK] Registered scheduled task '$TaskName' (S4U / Highest, Daily 09:00)"
