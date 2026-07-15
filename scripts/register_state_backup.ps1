# Registers (or re-registers) the JarvisStateBackup scheduled task (DEV-16).
# Mirrors register_morning_digest.ps1: S4U + RunLevel Highest so it runs
# whether the user is logged on or not (session-independent, reboot-resistant
# — same JarvisOpsWatchdog pattern the task spec calls for). StartWhenAvailable
# catches up a run skipped while the PC was off.
#
# Registration does NOT run it now -- run once to verify:
#   schtasks /Run /TN JarvisStateBackup
# or wait for the daily 03:30 trigger.

$ErrorActionPreference = 'Stop'
$TaskName = 'JarvisStateBackup'
$Python   = 'C:\jarvis\.venv\Scripts\python.exe'
$Script   = 'C:\jarvis\scripts\state_backup.py'

$psArgs = '"{0}"' -f $Script
$Action = New-ScheduledTaskAction -Execute $Python -Argument $psArgs -WorkingDirectory 'C:\jarvis'

# Workgroup (non-domain) machine: qualify the local account with the computer name.
$Account = "$env:COMPUTERNAME\$env:USERNAME"

# Daily at 03:30 — ahead of JarvisMorningDigest (09:00), clear of the legacy
# scripts/daily_backup.ps1 local-disk-only copy at 03:00.
$Trigger = New-ScheduledTaskTrigger -Daily -At 3:30am

$Principal = New-ScheduledTaskPrincipal -UserId $Account `
    -LogonType S4U -RunLevel Highest

# Run a missed schedule as soon as possible; retry a few times on transient
# failure (R2 hiccup); a single run must not overlap another.
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 10) `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 15)
$Settings.DisallowStartIfOnBatteries = $false
$Settings.StopIfGoingOnBatteries     = $false

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
    -Principal $Principal -Settings $Settings -Force | Out-Null

Write-Host "[OK] Registered scheduled task '$TaskName' (S4U / Highest, Daily 03:30)"
