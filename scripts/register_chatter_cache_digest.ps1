# Registers (or re-registers) the JarvisChatterCacheDigest scheduled task.
# Mirrors JarvisErrorDigest: S4U + RunLevel Highest so it runs whether the user
# is logged on or not (session-independent, reboot-resistant).
# StartWhenAvailable catches up a run skipped while the PC was off.
#
# Что делает таск: раз в сутки печатает hit-rate prompt-кэша chatter раздельно
# по brain/classifier (с базой, не только процентом) и шумит владельцу ТОЛЬКО
# при сигнатуре регрессии — N промахов подряд при живом кэше.
#
# Зачем таск, а не «запускать руками»: 2026-07-23 профиль въехал в кэшируемый
# префикс классификатора, кэш умер, счёт вырос на треть — и это прожило сутки
# незамеченным, потому что смотреть было некуда. Мониторинг, который надо
# вспомнить запустить, наполовину мёртв.
#
# Registration does NOT run it now -- run once to verify:
#   schtasks /Run /TN JarvisChatterCacheDigest

$ErrorActionPreference = 'Stop'
$TaskName = 'JarvisChatterCacheDigest'
$Python   = 'C:\jarvis\.venv\Scripts\python.exe'
$Script   = 'C:\jarvis\scripts\chatter_cache_digest.py'

# `-X utf8` (DEV-58): диагностика этой задачи уходит в stdout, который
# Планировщик не хранит. Причину отказа читают, повторяя прогон руками, — и
# если вывод рвётся консольной кодировкой, причина теряется ВТОРОЙ раз,
# ровно в аварии. Не обёртка (проглотит код возврата) и не переменная
# окружения (действует незаметно и на всё сразу).
# Сверка: app/services/task_encoding.py.
$psArgs = '-X utf8 "{0}"' -f $Script
$Action = New-ScheduledTaskAction -Execute $Python -Argument $psArgs -WorkingDirectory 'C:\jarvis'

# Workgroup (non-domain) machine: qualify the local account with the computer name.
$Account = "$env:COMPUTERNAME\$env:USERNAME"

# Daily at 21:05 — через 5 минут после JarvisErrorDigest, чтобы два TG-сообщения
# не приходили в одну секунду и читались как разные вещи.
$Trigger = New-ScheduledTaskTrigger -Daily -At 9:05pm

$Principal = New-ScheduledTaskPrincipal -UserId $Account `
    -LogonType S4U -RunLevel Highest

$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 10) `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
$Settings.DisallowStartIfOnBatteries = $false
$Settings.StopIfGoingOnBatteries     = $false

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
    -Principal $Principal -Settings $Settings -Force | Out-Null

# ── ПРОВЕРКА ФАКТОМ ─────────────────────────────────────────────────────────
# Перечитываем ЖИВУЮ задачу, а не считаем регистрацию удавшейся по
# отсутствию исключения. 23.08 живая копия JarvisDrillNightly разошлась с
# файлом: «записано» и «стоит в системе» — разные утверждения.
$live = Get-ScheduledTask -TaskName $TaskName
$liveArgs = ($live.Actions | Select-Object -First 1).Arguments
if ($liveArgs -notmatch '(?<![\w-])-X\s+utf8(?![\w])') {
    throw "[FAIL] в живой задаче '$TaskName' нет '-X utf8': $liveArgs"
}

Write-Host "[OK] Registered scheduled task '$TaskName' (S4U / Highest, Daily 21:05)"
Write-Host "[OK] прочитано обратно: $liveArgs"
