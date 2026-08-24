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

# -LeadPeer — id аккаунта, которому лид пишет реплики сценария. ОБЯЗАТЕЛЕН
# и сверяется с `.secrets/drill_lead_peers.txt`.
#
# Почему обязателен, а не «как-нибудь по умолчанию»: разрешённых
# получателей в файле СЕЙЧАС ДВА (Ольга и Ярина), и `resolve_peer` в
# drill_nightly.py в этом случае отказывается угадывать — падает с
# «задать --lead-peer явно». Регистрация без него создала бы задачу,
# которая гарантированно умрёт в 10:00 в stdout, который никто не
# хранит. Ровно так этот файл и разошёлся с живой задачей: id дописали
# в систему руками, а сюда — нет.
#
# [long], а не [int]: telegram-id не влезает в Int32.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [long]$LeadPeer,
    [switch]$Disabled
)

$ErrorActionPreference = 'Stop'
$TaskName = 'JarvisDrillNightly'
$Python   = 'C:\jarvis\.venv\Scripts\python.exe'
$Script   = 'C:\jarvis\scripts\drill_nightly.py'
$At       = '10:00'

if (-not (Test-Path $Python)) { throw "нет интерпретатора $Python" }
if (-not (Test-Path $Script)) { throw "нет скрипта $Script" }

# Фейл-клоуз по получателю. Список тот же, что читает сам скрипт, —
# ошибку в id надо слышать ЗДЕСЬ и вслух, а не через сутки кодом 2.
$PeersFile = 'C:\jarvis\.secrets\drill_lead_peers.txt'
if (-not (Test-Path $PeersFile)) { throw "нет файла разрешений $PeersFile — некому слать" }
$Peers = @(Get-Content -LiteralPath $PeersFile -Encoding UTF8 |
    ForEach-Object { ($_ -split '#', 2)[0].Trim() } |
    Where-Object { $_ -match '^\d+$' } |
    ForEach-Object { [long]$_ })
if ($Peers.Count -eq 0) { throw "$PeersFile пуст — вписать id получателя" }
if ($Peers -notcontains $LeadPeer) {
    throw "[FAIL] -LeadPeer $LeadPeer не в $PeersFile (разрешённых: $($Peers.Count))"
}

# `-X utf8` (DEV-58): вердикт дрила уходит в stdout, который Планировщик
# не хранит. Причину отказа читают, повторяя прогон руками, — и если вывод
# рвётся консольной кодировкой, причина теряется ВТОРОЙ раз, ровно в
# аварии: 23.08 ночной дрил вернул код 2 без единого слова о причине.
# Сверка: app/services/task_encoding.py.
$psArgs = '-X utf8 "{0}" --lead-peer {1}' -f $Script, $LeadPeer
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

# Аргументы читаем обратно ИЗ СИСТЕМЫ. Прежняя проверка стерегла триггер и
# принципала, а строку запуска — нет, и расхождение файла с живой задачей
# прожило незамеченным.
$liveArgs = ($live.Actions | Select-Object -First 1).Arguments
if ($liveArgs -notmatch '(?<![\w-])-X\s+utf8(?![\w])') {
    throw "[FAIL] в живой задаче нет '-X utf8': $liveArgs"
}
if ($liveArgs -notmatch ("--lead-peer\s+" + [regex]::Escape([string]$LeadPeer) + '(?!\d)')) {
    throw "[FAIL] в живой задаче нет '--lead-peer $LeadPeer': $liveArgs"
}

$state = $live.State
Write-Host "[OK] '$TaskName': S4U/Highest, ежедневно $At (внутри окна 9-20), IgnoreNew"
Write-Host "[OK] состояние: $state; следующий запуск: $($info.NextRunTime)"
Write-Host "[OK] прочитано обратно: $liveArgs"
if ($state -eq 'Disabled') {
    Write-Host "[i] таск ВЫКЛЮЧЕН. Включить: Enable-ScheduledTask -TaskName $TaskName"
}
