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

# P16-а, ЛОВУШКА 1 «кто сторожит сторожа». Этому таску поручены пробы chatter
# (chatter_runner / chatter_guardian), то есть он — ЕДИНСТВЕННЫЙ канал, который
# скажет о смерти гардиана. Без самоподъёма мы бы просто перенесли дыру 1 на
# уровень выше: 29.07 факт был именно такой — у гардиана AtStartup+AtLogOn+PT10M,
# а у этого таска только AtStartup+AtLogOn, то есть до перезагрузки.
#
# БЕССРОЧНО = `-RepetitionInterval` БЕЗ `-RepetitionDuration`. НЕ передавать
# `([TimeSpan]::MaxValue)`: сериализуется в `P99999999DT23H59M59S`, валидатор
# XML отвергает (`HRESULT 0x80041318`), Register-ScheduledTask падает, и таск
# молча остаётся со старым определением — ровно так деплой 25.07 оставил дыру
# открытой у гардиана.
#
# Безопасно ровно потому, что второй экземпляр сам выходит по PID-локу
# (ops_watchdog_detached.ps1: 'another ops watchdog already running'), а
# планировщик держит MultipleInstances IgnoreNew — две линии защиты.
# AtStartup/AtLogOn ОСТАЮТСЯ: после ребута сторож должен встать сразу.
$Repeat = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 10)

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

# ПРОВЕРКА ФАКТОМ, а не кодом возврата: провал регистрации виден только в живом
# XML. Без неё следующий деплой снова оставит дыру молча (прецедент 25.07).
$reg = Get-ScheduledTask -TaskName $TaskName
$rep = @($reg.Triggers | Where-Object { $_.Repetition.Interval })
if ($rep.Count -eq 0) {
    throw "[FAIL] Планировщик не принял повторяющийся триггер для '$TaskName' - самоподъёма НЕТ"
}

Write-Host "[OK] Registered scheduled task '$TaskName' (S4U / Highest, AtStartup + AtLogOn + repeat)"
Write-Host ("     Repetition: {0}, duration '{1}' (пусто = бессрочно)" -f $rep[0].Repetition.Interval, $rep[0].Repetition.Duration)
