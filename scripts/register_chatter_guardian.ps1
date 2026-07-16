# Registers (or re-registers) the JarvisChatterGuardian scheduled task.
# Mirrors JarvisBotGuardian / JarvisBackendGuardian: S4U + RunLevel Highest so it
# runs whether the user is logged on or not (session-independent), so the chatter
# userbot recovers on a HEADLESS reboot (e.g. Windows Update) without an
# interactive desktop logon. MultipleInstances=IgnoreNew; triggers at startup AND
# at logon. Registration does NOT start it -- run `schtasks /Run /TN JarvisChatterGuardian`.

$ErrorActionPreference = 'Stop'
$TaskName = 'JarvisChatterGuardian'
$Script   = 'C:\jarvis\scripts\chatter_guardian_detached.ps1'

$psArgs = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $Script
$Action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $psArgs -WorkingDirectory 'C:\jarvis'

# Workgroup (non-domain) machine: qualify the local account with the computer name.
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
Write-Host "     Start it now with: schtasks /Run /TN JarvisChatterGuardian"
