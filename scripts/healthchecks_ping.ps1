<#
.SYNOPSIS
  Dead-man пінг у healthchecks.io — зовнішній сторож ферми.

.DESCRIPTION
  ЛОГІКА DEAD-MAN, а не «сповістити про помилку»: скрипт пінгує ТІЛЬКИ КОЛИ ВСЕ
  ЖИВЕ. Якщо машина померла, заснула, втратила мережу або сам скрипт зламався —
  пінга не буде, і healthchecks.io здійме тривогу САМ, ззовні. Саме тому сторож
  зовнішній: він ловить те, чого внутрішній моніторинг за визначенням не бачить
  (docs/jarvis-panel/PHASE0_READONLY.md §0).

  Перевіряє: свіжість heartbeat-файлів і наявність процесів ферми.
  Здорово   -> GET <URL>            (+ пише state/healthchecks_last.txt)
  Хворо     -> GET <URL>/fail       (тіло = що саме не так)
  Немає URL -> нічого не робить, код 0 (сторож просто не налаштований)

.NOTES
  Read-only: нічого не перезапускає і не вбиває. Це сигналізація, не гардіан.
#>
[CmdletBinding()]
param(
  [string]$Url = $env:HEALTHCHECKS_URL,
  [string]$Root = $(if ($env:JARVIS_ROOT) { $env:JARVIS_ROOT } else { 'C:\jarvis' }),
  [int]$FreshSeconds = 180,
  [switch]$WhatIfOnly
)

$ErrorActionPreference = 'Stop'

# Оголошуємо кодування консолі як UTF-8 (DEV-59, сторона ЧИТАЧА).
# Під Планувальником консоль стартує в cp866 — заміряно 24.08 пробною
# задачею, — і кириличний рядок цього виводу приїжджає абракадаброю саме
# тоді, коли його читають: в аварії. Стоїть РАНІШЕ першого друку у файлі
# (нижче вже `Write-Output`). Посередника (`PYTHONUTF8`) тут немає і бути
# не може: питона цей скрипт не запускає взагалі.
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

if ([string]::IsNullOrWhiteSpace($Url)) {
  Write-Output 'HEALTHCHECKS_URL не заданий — сторож не налаштований, вихід 0.'
  exit 0
}

$problems = @()

# 1) heartbeat-файли. ВІК беремо з LastWriteTime, а не з розміру: на NTFS розмір
#    живого лога бреше (0 байт при відкритому write-хендлі).
#    ДВІ ФОРМИ ВІДМІТКИ ЖИВОСТІ chatter-раннера, і знати треба обидві:
#      легаси          — один безіменний раннер писав chatter_heartbeat.txt
#      мультиклієнтна  — раннер клієнта пише chatter_heartbeat_<slug>.txt
#    16.08 17:08 піднявся мультиклієнтний гардіан, і легаси-файл з тієї хвилини
#    не чіпає НІХТО. Той самий дефект уже ловили двічі: в ops_watchdog.py
#    (16.08) і в панелі клієнта (18.08, екран Ольги двоє діб брехав «Немає
#    зв'язку» при живому боті). Тут він лежав ТИХО, бо HEALTHCHECKS_URL не
#    заданий і скрипт виходить нулем вище — тобто зброя була б несправна рівно
#    в день, коли її зарядять.
#    Беремо НАЙСВІЖІШУ з відомих форм і НАЗИВАЄМО файл у тексті проблеми:
#    «heartbeat застарів» без імені — це те саме мовчання, тільки голосніше.
$beats = @{
  'chatter раннер'   = @('chatter_heartbeat_*.txt', 'chatter_heartbeat.txt')
  'головний бот'     = @('bot_heartbeat.txt')
  'chatter гардіан'  = @('chatter_guardian_heartbeat.txt')
  'ops_watchdog'     = @('ops_watchdog_heartbeat.txt')
}
$stateDir = Join-Path $Root 'state'
foreach ($name in $beats.Keys) {
  $found = @()
  foreach ($pat in $beats[$name]) {
    $found += @(Get-ChildItem -Path $stateDir -Filter $pat -File -ErrorAction SilentlyContinue)
  }
  $best = $found | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if (-not $best) { $problems += "$name : heartbeat відсутній"; continue }
  $age = ((Get-Date) - $best.LastWriteTime).TotalSeconds
  if ($age -gt $FreshSeconds) {
    $problems += ("{0} : heartbeat {1:N0} с тому ({2})" -f $name, $age, $best.Name)
  }
}

# 2) процеси. Фільтр по Name='python.exe' ОБОВ'ЯЗКОВИЙ: перевірка лише за
#    CommandLine матчить сам процес перевірки (рядок потрапляє в його командний
#    рядок) і дає фальшиве «живий» на мертвому раннері.
$selfIds = @($PID)
$procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
         Where-Object { $_.Name -eq 'python.exe' -and $_.ProcessId -notin $selfIds }
$expect = @{
  'chatter раннер' = 'chatter.telethon_run'
  'backend'        = 'run_backend_detached'
  'головний бот'   = 'jarvis_smart_telegram_control'
}
foreach ($name in $expect.Keys) {
  $hit = $procs | Where-Object { $_.CommandLine -like "*$($expect[$name])*" }
  if (-not $hit) { $problems += "$name : процес не знайдено" }
}

# 3) СВЯЗЬ НАРУЖУ (§5.2 спеки 27.08). FAIL-CLOSED.
#
#    ЗАЧЕМ. До этой правки «здоровьем» считались ровно две вещи: свежесть
#    heartbeat-файлов и наличие процессов. Ни одной проверки того, что машина
#    способна достучаться наружу. 26–27.08 всё это было зелёным ровно в те
#    20 часов, когда ни один провод не работал.
#
#    В ту аварию пронесло: перехват был ПОЛНЫМ, «ok» не уехал, и тишина
#    сработала как надо. Но при ЧАСТИЧНОМ перехвате — hc-ping.com доступен,
#    api.telegram.org нет — скрипт отправил бы бодрое «ok» и АКТИВНО ПОГАСИЛ
#    тревогу, пока бот нем. Это опаснее самой аварии: там мы молчали, здесь мы
#    сказали бы «всё хорошо».
#
#    FAIL-CLOSED БУКВАЛЬНО: нет файла, не разобрался, протух, `measured=false` —
#    ВСЁ ЭТО ПРОБЛЕМА, а не «нет данных». «Не смогли спросить» и «спросили,
#    чисто» — разные ответы, и подменять первый вторым значит гасить сторожа
#    ровно тогда, когда он нужнее всего.
#
#    ПОРОГ ТОТ ЖЕ `$FreshSeconds`, что и у heartbeat'ов, и второго числа тут
#    заводить нельзя: два числа на одну вещь — и меньшее гасит большее молча.
#    Цикл ops_watchdog 30 с, то есть 180 с — это шесть циклов запаса, и всё это
#    втрое меньше 30-минутного окна слепоты, назначенного владельцем.
$reachFile = Join-Path $stateDir 'reachability_verdict.json'
if (-not (Test-Path $reachFile)) {
  $problems += 'связь наружу : вердикта нет (ops_watchdog не писал reachability_verdict.json)'
} else {
  $reach = $null
  try { $reach = Get-Content $reachFile -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop } catch { $reach = $null }
  if ($null -eq $reach) {
    $problems += 'связь наружу : вердикт не разобрался (битый reachability_verdict.json)'
  } else {
    # СОСТАВ ПРОВОДОВ БЕРЁТСЯ ИЗ `terms`, а не из готового списка красных.
    # Готовый список был бы вторым источником правды о том же факте, и разошёлся
    # бы он с `terms` молча — ровно в тот день, когда добавят четвёртый провод.
    $terms = $null
    if ($null -ne $reach.terms) { $terms = $reach.terms }
    $names = @()
    if ($null -ne $terms) { $names = @($terms.PSObject.Properties.Name) }

    if ($names.Count -eq 0) {
      # Пустой вердикт и вердикт из зелёных проводов — РАЗНЫЕ состояния.
      # Склеить их в «всё хорошо» значило бы отдать зелёное там, где не
      # измерено ничего.
      $problems += 'связь наружу : не измерено ни одного провода'
    } else {
      # Возраст считается в UNIX-времени с обеих сторон: `ts` пишет python
      # через `time.time()`, и переводить его в локальное время значило бы
      # завести часовой пояс третьим участником сверки.
      $reachAge = ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds()) - [double]$reach.ts
      if ($reachAge -gt $FreshSeconds) {
        $problems += ("связь наружу : вердикт устарел, {0:N0} с тому (порог {1} с)" -f $reachAge, $FreshSeconds)
      }
      # Красные провода НАЗЫВАЮТСЯ ПОИМЁННО, каждый своей строкой. «Связи нет»
      # без имени не чинится: чинить api.telegram.org и чинить R2 — разные
      # действия. Одной строкой со списком тоже нельзя: склейка N проводов в
      # одно сообщение — тот же агрегат, только на стороне текста.
      foreach ($n in $names) {
        $t = $terms.$n
        # `ok` отсутствует → провод считается МЁРТВЫМ. Отсутствие поля это
        # «не знаем», а «не знаем» здесь обязано читаться в сторону тревоги.
        $wireOk = $false
        if ($null -ne $t -and $null -ne $t.ok) { $wireOk = [bool]$t.ok }
        if (-not $wireOk) {
          $problems += ("связь наружу : провод {0} не отвечает" -f $n)
        }
      }
    }
  }
}

$healthy = ($problems.Count -eq 0)
$body = if ($healthy) { 'ok' } else { ($problems -join '; ') }

if ($WhatIfOnly) {
  Write-Output ("healthy={0}`n{1}" -f $healthy, $body)
  exit 0
}

$target = if ($healthy) { $Url } else { ($Url.TrimEnd('/') + '/fail') }
try {
  Invoke-RestMethod -Uri $target -Method Post -Body $body -TimeoutSec 20 | Out-Null
  if ($healthy) {
    # Позначку читає панель Джарвіса, щоб показати вік останнього пінга.
    # ⚠️ НЕ `Get-Date -UFormat %s`: у PowerShell 5.1 воно рахує епоху від
    # ЛОКАЛЬНОГО часу, тому в Києві (UTC+3) давало мітку на 10800 с у
    # МАЙБУТНЄ, і будь-який споживач діставав ВІД'ЄМНИЙ вік. Спіймано живцем
    # 2026-07-31: записало 06:03:52 при реальних 03:03:51.
    # ToUnixTimeSeconds() рахує від UTC за визначенням і зсуву не має.
    $stamp = Join-Path $Root 'state\healthchecks_last.txt'
    Set-Content -Path $stamp -Value ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds()) -Encoding utf8
  }
  Write-Output ("пінг {0}: {1}" -f $(if ($healthy) { 'ok' } else { 'FAIL' }), $body)
} catch {
  # Мережа лягла — це САМЕ той випадок, заради якого сторож зовнішній:
  # пінга не буде, і healthchecks.io здійме тривогу сам.
  Write-Output "пінг не пройшов: $($_.Exception.Message)"
  exit 1
}
