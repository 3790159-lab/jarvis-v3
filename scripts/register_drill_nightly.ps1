# Регистрирует (или перерегистрирует) таск JarvisDrillNightly — регресс дрила
# по расписанию (стенд v2, Э5b). Образец — JarvisChatterCacheDigest: S4U +
# RunLevel Highest, чтобы таск жил без залогиненного пользователя.
#
# Что делает таск: внутри рабочего окна персоны сбрасывает дрил-контакт,
# гоняет Д-10 через `drill_runner --auto-lead` (человек не нужен) и присылает
# владельцу вердикт. Гейт перед мержем ловит НАШИ правки; этот прогон ловит
# дрейф модели и внешние поломки, которых гейт не видит вовсе (классификатор
# 26.07 упал не от нашей правки).
#
# Время: 10:00 — ВНУТРИ work_hours volska (9-20). Вне окна скрипт сам выходит
# с кодом 3, ничего не тратя и ничего не стирая.
#
# Деньги: ~$0.26 за прогон (1 холодный ход + 4 тёплых), ~$8/мес.
#
# -Disabled  — зарегистрировать выключенным (по умолчанию таск ВКЛЮЧЁН).
# Включить/выключить потом:
#   Enable-ScheduledTask  -TaskName JarvisDrillNightly
#   Disable-ScheduledTask -TaskName JarvisDrillNightly
# Проверить руками (потратит деньги, если внутри окна):
#   schtasks /Run /TN JarvisDrillNightly

param([switch]$Disabled)

$ErrorActionPreference = 'Stop'
$TaskName = 'JarvisDrillNightly'
$Python   = 'C:\jarvis\.venv\Scripts\python.exe'
$Script   = 'C:\jarvis\scripts\drill_nightly.py'
$At       = '10:00'

if (-not (Test-Path $Python)) { throw "нет интерпретатора $Python" }
if (-not (Test-Path $Script)) { throw "нет скрипта $Script" }

$psArgs = '"{0}"' -f $Script
$Action = New-ScheduledTaskAction -Execute $Python -Argument $psArgs -WorkingDirectory 'C:\jarvis'

# Workgroup-машина: локальную учётку квалифицируем именем компьютера.
$Account = "$env:COMPUTERNAME\$env:USERNAME"

$Trigger = New-ScheduledTaskTrigger -Daily -At $At

$Principal = New-ScheduledTaskPrincipal -UserId $Account `
    -LogonType S4U -RunLevel Highest

# ExecutionTimeLimit 40 мин: прогон Д-10 укладывается в 10 (приёмка §9 п.6),
# запас — на человеческие паузы лида и медленный ход. IgnoreNew, чтобы
# зависший прогон не наложился на следующий.
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 40)
$Settings.DisallowStartIfOnBatteries = $false
$Settings.StopIfGoingOnBatteries     = $false

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
    -Principal $Principal -Settings $Settings -Force | Out-Null

if ($Disabled) { Disable-ScheduledTask -TaskName $TaskName | Out-Null }

# ── ПРОВЕРКА ФАКТОМ ──────────────────────────────────────────────────────────
# Деплой 25.07 молча оставил гардиану дыру именно потому, что регистрация
# считалась успешной по отсутствию исключения. Перечитываем ЖИВОЙ таск.
$live = Get-ScheduledTask -TaskName $TaskName
$info = $live | Get-ScheduledTaskInfo

$daily = @($live.Triggers | Where-Object { $_.CimClass.CimClassName -eq 'MSFT_TaskDailyTrigger' })
if ($daily.Count -lt 1) { throw "[FAIL] у '$TaskName' нет ежедневного триггера" }
$startHour = ([datetime]$daily[0].StartBoundary).Hour
if ($startHour -lt 9 -or $startHour -ge 20) {
    throw "[FAIL] старт $startHour`:xx вне рабочего окна volska 9-20"
}
if ($live.Principal.LogonType -ne 'S4U')      { throw "[FAIL] LogonType $($live.Principal.LogonType), ожидался S4U" }
if ($live.Principal.RunLevel  -ne 'Highest')  { throw "[FAIL] RunLevel $($live.Principal.RunLevel)" }
if ($live.Settings.MultipleInstances -ne 'IgnoreNew') { throw "[FAIL] MultipleInstances $($live.Settings.MultipleInstances)" }

$state = $live.State
Write-Host "[OK] '$TaskName': S4U/Highest, ежедневно $At (внутри окна 9-20), IgnoreNew"
Write-Host "[OK] состояние: $state; следующий запуск: $($info.NextRunTime)"
if ($state -eq 'Disabled') {
    Write-Host "[i] таск ВЫКЛЮЧЕН. Включить: Enable-ScheduledTask -TaskName $TaskName"
}
