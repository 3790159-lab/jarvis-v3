# Registers (or re-registers) the JarvisOpsWatchdog scheduled task.
# Mirrors register_bot_guardian.ps1 / register_backend_guardian.ps1: S4U +
# RunLevel Highest so it runs whether the user is logged on or not
# (session-independent), so the independent monitor survives a HEADLESS reboot
# without an interactive desktop logon — the exact gap that let the backend die
# at 03:14 unnoticed. MultipleInstances=IgnoreNew; triggers at system startup
# AND at logon.
# Registration does NOT start it -- run `schtasks /Run /TN JarvisOpsWatchdog`.

$ErrorActionPreference = 'Stop'
$TaskName = 'JarvisOpsWatchdog'
$Script   = 'C:\jarvis\scripts\ops_watchdog_detached.ps1'

$psArgs = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $Script
$Action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $psArgs -WorkingDirectory 'C:\jarvis'

# Workgroup (non-domain) machine: qualify the local account with the computer
# name so the SID maps (USERDOMAIN would wrongly be "WORKGROUP" here).
$Account = "$env:COMPUTERNAME\$env:USERNAME"

$Triggers = @(
    New-ScheduledTaskTrigger -AtStartup
    New-ScheduledTaskTrigger -AtLogOn -User $Account
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

Write-Host "[OK] Registered scheduled task '$TaskName' (S4U / Highest, AtStartup + AtLogOn)"
