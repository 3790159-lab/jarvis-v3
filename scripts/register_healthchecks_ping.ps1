# Registers (or re-registers) the JarvisHealthchecksPing scheduled task.
#
# Зовнішній dead-man: раз на 5 хвилин ганяє scripts\healthchecks_ping.ps1, який
# пінгує healthchecks.io ТІЛЬКИ КОЛИ ВСЕ ЖИВЕ. Машина померла / заснула /
# втратила мережу / сам скрипт зламався — пінга немає, і тривогу здіймає СЕРВІС
# ЗЗОВНІ. Внутрішній моніторинг цей клас відмов не ловить за визначенням: дрил 5
# (2026-07-31) показав це наочно — ops_watchdog був мертвий 7 хвилин, і
# повідомити про це зсередини не було кому.
#
# Period 5 min / Grace 10 min на боці healthchecks.io => тривога після ~15 хв
# тиші. Інтервал таска мусить лишатися 5 хв, інакше Grace не сходиться.
#
# S4U + RunLevel Highest — як у решти тасків ферми: працює без інтерактивного
# логона, тобто переживає headless-ребут.
# Registration does NOT start it -- run `schtasks /Run /TN JarvisHealthchecksPing`.

$ErrorActionPreference = 'Stop'
$TaskName = 'JarvisHealthchecksPing'
$Script   = 'C:\jarvis\scripts\healthchecks_ping.ps1'

if (-not (Test-Path $Script)) { throw "[FAIL] Немає скрипта пінга: $Script" }

# URL НЕ зашивається в дію таска: він живе в змінній оточення користувача
# (HKCU\Environment), щоб не потрапити ні в репозиторій, ні в XML таска, який
# читається будь-ким. Скрипт сам бере $env:HEALTHCHECKS_URL і при порожньому
# значенні тихо виходить кодом 0 (сторож просто не налаштований).
$psArgs = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $Script
$Action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $psArgs -WorkingDirectory 'C:\jarvis'

# Workgroup (non-domain): кваліфікуємо локальний акаунт іменем машини.
$Account = "$env:COMPUTERNAME\$env:USERNAME"

# БЕЗСТРОКОВО = -RepetitionInterval БЕЗ -RepetitionDuration.
# НЕ передавати [TimeSpan]::MaxValue: серіалізується в P99999999DT23H59M59S,
# валідатор XML відкидає (HRESULT 0x80041318), Register-ScheduledTask падає, а
# таск МОВЧКИ лишається зі старим визначенням. Саме так деплой 25.07 лишив
# дірку відкритою у гардіана, і тест був зелений, бо читав текст скрипта.
$Repeat = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 5)

$Triggers = @(
    New-ScheduledTaskTrigger -AtStartup
    New-ScheduledTaskTrigger -AtLogOn -User $Account
    $Repeat
)

$Principal = New-ScheduledTaskPrincipal -UserId $Account `
    -LogonType S4U -RunLevel Highest

# ExecutionTimeLimit 4 хв (< інтервалу): зависла мережа не має права заблокувати
# всі наступні пінги через MultipleInstances IgnoreNew. Зависання саме по собі
# дає тишу => зовнішню тривогу, але відновитися воно мусить само.
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 4)
$Settings.DisallowStartIfOnBatteries = $false
$Settings.StopIfGoingOnBatteries     = $false

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Triggers `
    -Principal $Principal -Settings $Settings -Force | Out-Null

# ПЕРЕВІРКА ФАКТОМ, а не кодом повернення: провал реєстрації видно лише в живому
# XML. Перечитуємо те, що реально прийняв Планувальник.
$reg = Get-ScheduledTask -TaskName $TaskName
$rep = @($reg.Triggers | Where-Object { $_.Repetition.Interval })
if ($rep.Count -eq 0) {
    throw "[FAIL] Планувальник не прийняв повторюваний тригер для '$TaskName' — пінга раз на 5 хв НЕМАЄ"
}
if ($rep[0].Repetition.Interval -ne 'PT5M') {
    throw "[FAIL] Інтервал '$($rep[0].Repetition.Interval)', очікували PT5M — Grace 10 хв не зійдеться"
}
if ($rep[0].Repetition.Duration) {
    throw "[FAIL] Тривалість '$($rep[0].Repetition.Duration)' — повторення не безстрокове, пінги колись просто припиняться"
}

Write-Host "[OK] Registered scheduled task '$TaskName' (S4U / Highest, AtStartup + AtLogOn + repeat)"
Write-Host ("     Repetition: {0}, duration '{1}' (пусто = безстроково)" -f $rep[0].Repetition.Interval, $rep[0].Repetition.Duration)
