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

$psArgs = '"{0}"' -f $Script
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

Write-Host "[OK] Registered scheduled task '$TaskName' (S4U / Highest, Daily 21:05)"
