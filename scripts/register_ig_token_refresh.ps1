# Registers (or re-registers) the JarvisIgTokenRefresh scheduled task.
# Mirrors the guardians: S4U + RunLevel Highest so it runs whether the user is
# logged on or not (session-independent). Unlike the guardians this is NOT a
# keep-alive loop -- it fires once daily; the script itself age-gates (>=30d
# since last successful refresh) so a missed day never lets the token lapse.
# StartWhenAvailable catches up a run skipped while the PC was off.
#
# Registration does NOT run it now -- run once to verify:
#   schtasks /Run /TN JarvisIgTokenRefresh
# or wait for the daily trigger. Add --force by editing $psArgs to bypass the gate.

$ErrorActionPreference = 'Stop'
$TaskName = 'JarvisIgTokenRefresh'
$Python   = 'C:\jarvis\.venv\Scripts\python.exe'
$Script   = 'C:\jarvis\scripts\ig_token_refresh.py'

$psArgs = '"{0}"' -f $Script
$Action = New-ScheduledTaskAction -Execute $Python -Argument $psArgs -WorkingDirectory 'C:\jarvis'

# Workgroup (non-domain) machine: qualify the local account with the computer name.
$Account = "$env:COMPUTERNAME\$env:USERNAME"

# Daily at 04:17 (quiet hour). The script's age-gate decides whether to act.
$Trigger = New-ScheduledTaskTrigger -Daily -At 4:17am

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

Write-Host "[OK] Registered scheduled task '$TaskName' (S4U / Highest, Daily 04:17, age-gated >=30d)"
