#Requires -RunAsAdministrator
<#
Кто обрывает бота — ЗАМЕР, называющий инициатора по имени.

## Почему не аудит Windows (тупик, пройденный 18.08)

Первая проба вешала SACL на процесс бота и включала подкатегории аудита
Kernel Object + Process Termination. SACL встал (`S:(AU;SA;CC;;;WD)` читается
обратно с живого процесса), политика включилась (`auditpol /get /r` →
Success), а событий 4656 не появилось НИ ОДНОГО — даже на живом смоуке, где
хэндл с правом PROCESS_TERMINATE открывался намеренно.

Вывод замера: объект-ПРОЦЕСС этой подкатегорией не аудитится, сколько SACL на
него ни вешай. Событие 4689 при этом пишется, но инициатора не называет: его
Subject — учётка самого умершего.

Записано здесь, а не выброшено: дорога выглядит правильной в любом
руководстве, и следующий, кто пойдёт искать убийцу, потратит на неё тот же
час.

## Что работает

ETW-провайдер `Microsoft-Windows-Kernel-Audit-API-Calls`
({e02a841c-75a3-4fa7-afc8-ae09cf9b7f23}) — он ровно про это:

| id | что это | поля |
|----|---------|------|
| 5 | NtOpenProcess | TargetProcessId, DesiredAccess, ReturnCode |
| 6 | NtOpenThread | TargetProcessId, TargetThreatId, DesiredAccess |
| 2 | NtTerminateProcess | TargetProcessId, ReturnCode, StartKey |

**Имя убийцы — в заголовке события:** `Execution ProcessID` это тот, КТО
вызвал, а `TargetProcessId` — кого. Это и есть ответ, которого не дал аудит.

## Прогон

    powershell -File scripts\kill_probe_etw.ps1 -Start    # начать запись
    powershell -File scripts\kill_probe_etw.ps1 -Report   # разобрать, не
                                                          # останавливая
    powershell -File scripts\kill_probe_etw.ps1 -Stop     # ОСТАНОВИТЬ

Сессия ETW живёт до `-Stop` или до перезагрузки. Она НЕ переживает ребут (не
autologger) — это осознанно: временная проба не должна пережить того, кто её
включил (DEV-37).
#>
[CmdletBinding()]
param(
    [switch]$Start,
    [switch]$Stop,
    [switch]$Report,
    # Кого искали. По умолчанию — бот; тот же признак, что у гардиана.
    [string]$Match = '*jarvis_smart_telegram_control*'
)

$ErrorActionPreference = 'Stop'

$SessionName = 'jarvis-killprobe'
$Provider    = 'Microsoft-Windows-Kernel-Audit-API-Calls'
$StateDir    = 'C:\jarvis\state\audit'
$Etl         = Join-Path $StateDir 'killprobe.etl'
$Xml         = Join-Path $StateDir 'killprobe.xml'

$EVT_TERMINATE = '2'
$EVT_OPEN      = '5'
$PROCESS_TERMINATE = 0x1

function Invoke-Start {
    if (-not (Test-Path $StateDir)) { New-Item -ItemType Directory -Path $StateDir -Force | Out-Null }
    & logman stop $SessionName -ets 2>$null | Out-Null      # хвост прошлой пробы
    if (Test-Path $Etl) { Remove-Item $Etl -Force }
    & logman create trace $SessionName -p $Provider 0xffffffffffffffff 0xff -ets `
        -o $Etl -nb 16 64 -bs 64 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "logman create вернул $LASTEXITCODE — запись НЕ идёт" }
    Write-Host "запись идёт: $SessionName -> $Etl"
    Write-Host "остановить: -Stop. Разобрать на ходу: -Report."
}

function Invoke-Stop {
    & logman stop $SessionName -ets | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Host "logman stop вернул $LASTEXITCODE (возможно, сессии уже нет)" }
    else { Write-Host "запись остановлена" }
}

function Get-Events {
    <# Разбор ETL. `tracerpt` не умеет читать файл, в который ещё пишут, —
       поэтому на ходу снимаем КОПИЮ, а не останавливаем сессию: остановка
       ради отчёта потеряла бы следующую смерть. #>
    if (-not (Test-Path $Etl)) { throw "нет $Etl — проба не запускалась" }
    $snapshot = Join-Path $StateDir 'killprobe.snapshot.etl'
    Copy-Item $Etl $snapshot -Force
    if (Test-Path $Xml) { Remove-Item $Xml -Force }
    & tracerpt $snapshot -o $Xml -of XML -y | Out-Null
    if (-not (Test-Path $Xml)) { throw "tracerpt не разобрал $snapshot" }
    $doc = [xml](Get-Content $Xml -Raw -Encoding UTF8)
    return @($doc.Events.Event | Where-Object {
        $_.System.Provider.Guid -eq '{e02a841c-75a3-4fa7-afc8-ae09cf9b7f23}' })
}

function Get-Field {
    param($Event, [string]$Name)
    $d = @($Event.EventData.Data | Where-Object { $_.Name -eq $Name })
    if ($d.Count -eq 0) { return $null }
    return ([string]$d[0].'#text').Trim()
}

function Get-KnownProcess {
    param([int64]$ProcId)
    if ($ProcId -ge 4294967295) { return "PID неизвестен (ядро)" }
    $p = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcId" -ErrorAction SilentlyContinue
    if ($p) {
        $cmd = [string]$p.CommandLine
        if ($cmd.Length -gt 110) { $cmd = $cmd.Substring(0, 110) + '…' }
        return "$($p.Name) — $cmd"
    }
    # Инициатор мог умереть сам (тест закончился, оболочка закрылась). Это не
    # провал замера: PID и время всё равно называют, ЧТО происходило.
    return "процесс уже мёртв — имя не восстановить"
}

function Invoke-Report {
    $events = Get-Events
    Write-Host "событий провайдера: $($events.Count)"

    # Живые боты + те, кого гардиан поднимал: PID мёртвого уже не спросишь у
    # системы, поэтому берём и журнал гардиана.
    $botPids = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like $Match } | ForEach-Object { $_.ProcessId })
    # 🔴 Только СЕГОДНЯШНИЕ подъёмы. Журнал гардиана живёт месяцами, и первый
    # заход собрал 230 PID за всю историю — а PID переиспользуются, поэтому
    # такой список объявляет «ботом» половину машины и отчёт врёт уверенно.
    $log = 'C:\jarvis\state\logs\bot_guardian.stdout.log'
    $today = (Get-Date -Format 'yyyy-MM-dd')
    if (Test-Path $log) {
        foreach ($line in (Get-Content $log -Tail 400)) {
            if ($line -notlike "*$today*") { continue }
            $m = [regex]::Match($line, 'launched bot \(PID (\d+)\)')
            if ($m.Success) { $botPids += [int]$m.Groups[1].Value }
        }
    }
    $botPids = $botPids | Sort-Object -Unique
    Write-Host "PID бота (живые + из журнала гардиана): $($botPids -join ', ')"

    Write-Host ""
    Write-Host "=== ЗАВЕРШЕНИЯ (NtTerminateProcess) ==="
    $found = $false
    foreach ($e in $events) {
        if ([string]$e.System.EventID -ne $EVT_TERMINATE) { continue }
        $target = Get-Field $e 'TargetProcessId'
        if (-not $target) { continue }
        # [int64]: у части событий вызывающий PID приходит как
        # 0xFFFFFFFF («неизвестен»), и [int] на нём падал — отчёт
        # обрывался на середине, показав ЧАСТЬ картины как всю.
        $caller = [int64][uint32]$e.System.Execution.ProcessID
        $isBot = $botPids -contains [int64]$target
        $mark = if ($isBot) { 'БОТ' } else { '   ' }
        if (-not $isBot) { continue }
        $found = $true
        Write-Host "$mark $($e.System.TimeCreated.SystemTime) | цель PID $target | ОБОРВАЛ PID $caller — $(Get-KnownProcess $caller)"
    }
    if (-not $found) { Write-Host "завершений бота в записи нет (ещё не умирал под пробой)" }

    Write-Host ""
    Write-Host "=== ОТКРЫТИЯ ХЭНДЛА БОТА С ПРАВОМ «УБИТЬ» (NtOpenProcess) ==="
    $opens = 0
    foreach ($e in $events) {
        if ([string]$e.System.EventID -ne $EVT_OPEN) { continue }
        $target = Get-Field $e 'TargetProcessId'
        if (-not $target -or ($botPids -notcontains [int64]$target)) { continue }
        # [uint32], а не [int]: PROCESS_ALL_ACCESS приезжает как 4294967295 и в
        # Int32 не влезает — отчёт падал ровно на самом интересном запросе,
        # том, где прав запрошено БОЛЬШЕ всего.
        $access = [int64][uint32](Get-Field $e 'DesiredAccess')
        if (($access -band $PROCESS_TERMINATE) -eq 0) { continue }
        $opens++
        # [int64]: у части событий вызывающий PID приходит как
        # 0xFFFFFFFF («неизвестен»), и [int] на нём падал — отчёт
        # обрывался на середине, показав ЧАСТЬ картины как всю.
        $caller = [int64][uint32]$e.System.Execution.ProcessID
        Write-Host "$($e.System.TimeCreated.SystemTime) | цель PID $target | access 0x$([Convert]::ToString([int64]$access,16)) | ОТКРЫЛ PID $caller — $(Get-KnownProcess $caller)"
    }
    if ($opens -eq 0) { Write-Host "никто не открывал бота с правом убить" }
}

if ($Start)  { Invoke-Start }
if ($Report) { Invoke-Report }
if ($Stop)   { Invoke-Stop }
if (-not ($Start -or $Stop -or $Report)) {
    Write-Host "нужен один из ключей: -Start | -Report | -Stop"
    exit 2
}
