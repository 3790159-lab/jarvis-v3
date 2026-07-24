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

# P16(в): САМОПОДЪЁМ. До 2026-07-25 триггеры были только AtStartup+AtLogOn, и
# смерть PowerShell-процесса гардиана в середине сессии лечилась ТОЛЬКО ребутом
# или новым логоном. Живой инцидент 20-22.07: гардиан умер, раннер умер следом,
# и почти двое суток его никто не поднял (форензика 25.07, PROBLEMS P16).
#
# Повторение КАЖДЫЕ 10 МИНУТ и БЕССРОЧНО ([TimeSpan]::MaxValue): конечная
# длительность означала бы, что через N часов дыра возвращается молча.
# Безопасно ровно потому, что второй экземпляр сам выходит по PID-локу
# (chatter_guardian_detached.ps1: 'another chatter guardian already running'),
# а планировщик держит MultipleInstances IgnoreNew — две линии защиты.
# AtStartup/AtLogOn ОСТАЮТСЯ: после ребута гардиан должен встать сразу, а не
# ждать первого тика повторения.
$Repeat = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 10) `
    -RepetitionDuration ([TimeSpan]::MaxValue)

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

Write-Host "[OK] Registered scheduled task '$TaskName' (S4U / Highest, AtStartup + AtLogOn)"
Write-Host "     Start it now with: schtasks /Run /TN JarvisChatterGuardian"
