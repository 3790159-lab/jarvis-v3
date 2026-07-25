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
# Повторение КАЖДЫЕ 10 МИНУТ и БЕССРОЧНО: конечная длительность означала бы,
# что через N часов дыра возвращается молча.
#
# БЕССРОЧНО = `-RepetitionInterval` БЕЗ `-RepetitionDuration`: в схеме
# Планировщика `<Repetition>` без `<Duration>` и есть "повторять бесконечно".
# НЕ передавать `([TimeSpan]::MaxValue)` — оно сериализуется в
# `P99999999DT23H59M59S`, валидатор XML отвергает его
# (`HRESULT 0x80041318`), Register-ScheduledTask падает, и таск молча остаётся
# со старым определением. Ровно так деплой 25.07 оставил дыру открытой,
# пока приёмка не полезла в живой XML (сторож: test_chatter_guardian_selfheal).
#
# Безопасно ровно потому, что второй экземпляр сам выходит по PID-локу
# (chatter_guardian_detached.ps1: 'another chatter guardian already running'),
# а планировщик держит MultipleInstances IgnoreNew — две линии защиты.
# AtStartup/AtLogOn ОСТАЮТСЯ: после ребута гардиан должен встать сразу, а не
# ждать первого тика повторения.
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

# Приёмка ФАКТОМ, а не по коду возврата: доказываем, что Планировщик принял
# третий триггер. Без этой проверки провал регистрации виден только в живом XML.
$reg = Get-ScheduledTask -TaskName $TaskName
$rep = @($reg.Triggers | Where-Object { $_.Repetition.Interval })
if ($rep.Count -lt 1) {
    throw "[FAIL] '$TaskName' зарегистрирован БЕЗ повторяющегося триггера — самоподъём (P16 в) не работает"
}
Write-Host "[OK] Registered scheduled task '$TaskName' (S4U / Highest, AtStartup + AtLogOn)"
Write-Host ("     Repetition: {0}, duration '{1}' (пусто = бессрочно)" -f $rep[0].Repetition.Interval, $rep[0].Repetition.Duration)
Write-Host "     Start it now with: schtasks /Run /TN JarvisChatterGuardian"
