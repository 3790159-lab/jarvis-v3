# Присмотр за КЛИЕНТСКОЙ панелью (один клиент = один процесс = один порт),
# полностью отвязанный от интерактивной сессии. Запускается задачей
# JarvisPanelClientGuardian (S4U / RunLevel Highest, AtStartup + AtLogOn) — так
# же, как JarvisBackendGuardian / JarvisBotGuardian / JarvisChatterGuardian.
#
# Спека: docs/superpowers/specs/2026-08-20-client-panel-supervisor.md
# (вариант B, §2.2 «исчерпанные отказы», §2.3 «пропал тайнет != панель умерла»).
#
# ЗАМЕР, ИЗ КОТОРОГО ЭТО РОДИЛОСЬ. 20.08 панель Ярины подняли вручную; через
# четыре минуты последняя запись в логе, через 27 минут порт 8011 не слушает
# никто. Traceback'а нет: панель не упала от своего кода — её сняли снаружи
# вместе с процессом-родителем (интерактивной сессией). Второй такой случай
# подряд. У трёх соседей по ферме гардиан есть, у панели клиента не было.
#
# ПОЧЕМУ ГАРДИАН, А НЕ «ЗАДАЧА С ПЕРЕЗАПУСКОМ ПРИ СБОЕ». Планировщик считает
# сбоем ненулевой код возврата. Панель, снятую taskkill'ом или вышедшую с rc 0,
# он не перезапустит, а зависший процесс (жив, не отвечает) для него вообще
# здоров. Именно эти два случая нас и убили.
#
# ВОКАБУЛЯР ВЕРДИКТА — ОБЩИЙ С ПРОБОЙ §3, И ЭТО ГЛАВНОЕ РЕШЕНИЕ ФАЙЛА.
# Гардиан не заводит своих слов: он рассуждает теми же четырьмя, что и
# `ops_watchdog.probe_panel_client`:
#     ok | no_response | http:<код> | no_bind_address
#
# ДВА РАЗНЫХ СЛОВА, И ПУТАТЬ ИХ НЕЛЬЗЯ:
#   `-Reason`  — ВЕРДИКТ ПРОБЫ, одно из четырёх слов выше. Живёт РОВНО в
#                Test-ShouldStartPanel и больше нигде;
#   `-Refusal` — ЧЕЛОВЕЧЕСКИЙ ТЕКСТ причины отказа, дословно из stdout панели
#                («JARVIS_PANELS_KEY не задан: ...»). Живёт в
#                Format-RefusalLine, приходит из Get-RefusalReason.
# Одно имя на оба понятия уже стояло здесь один круг, и цена ровно такая:
# первый, кто передаст в строку журнала слово `no_response`, получит запись,
# которая не называет НИ ОДНОЙ причины — а вся ветка заведена ради того, чтобы
# через час было видно, одна это ошибка или разные.
# Булево «жив/мёртв» здесь не годится ПО ПОСТРОЕНИЮ: §2.3 ввёл ТРЕТЬЕ
# состояние — «не могу измерить», — а два значения трёх состояний не выражают.
# На булеве слипание «не измерил» с «мертва» невозможно даже поймать, а именно
# это слипание и убивает здоровую панель.
#
# ЧЕТЫРЕ ГРАБЛИ, ЗАКРЫТЫЕ ПО ПОСТРОЕНИЮ:
#   1. Освобождение порта адресуется ВЛАДЕЛЬЦУ ПОРТА (Get-NetTCPConnection ->
#      OwningProcess), и только ему. Поиск процесса по подстроке его строки
#      запуска здесь ЗАПРЕЩЁН: под такой поиск попадает панель ДРУГОГО клиента
#      и pytest, который её импортирует. Ровно этот класс убил боевого бота
#      18.08 (DEV-38) — скрипт из worktree снёс прод по глобальной подстроке.
#   2. Гардиан не находит самого себя: решение принимается по порту и по
#      HTTP-ответу, а не по строке запуска, поэтому строке-маркеру просто
#      неоткуда взяться (ловушка «запуск != упоминание»).
#   3. Отказ старта != падение (§2.1/§2.2). run_panel_client.py при кривом
#      окружении печатает «ОТКАЗ, инстанс не поднят» и возвращает rc 1 — это
#      осознанный fail-closed. Перезапускать его раз в 15 секунд значит крутить
#      вечный цикл и ПРЯТАТЬ причину. Здесь: пауза BackoffSeconds, после
#      MaxRefusals подряд — длинный интервал LongRetrySeconds, и попытки НЕ
#      ПРЕКРАЩАЮТСЯ НИКОГДА. Жёсткий стоп требует человека у машины, а мы это
#      уже проходили: 16.08 гардиан вошёл в DOWN и не вышел 13 ч 42 мин.
#   4. Пропал тайнет != панель умерла (§2.3). resolve_client_host при
#      недоступном tailscale молча падает на петлю, живая панель при этом
#      слушает СТАРЫЙ тайнетовый адрес — и «не отвечает» означало бы убийство
#      здоровой панели. Такой случай зовётся no_bind_address, и гардиан на нём
#      НЕ ДЕЛАЕТ НИЧЕГО.
#
# АЛЕРТ В ТЕЛЕГРАМ ГАРДИАН НЕ ШЛЁТ (§2.2). Он пишет ОДНУ строку в свой журнал
# на вход в состояние исчерпанных отказов; настоящий алерт уходит от пробы §3
# через уже существующие `evaluate`/`alerted` в ops_watchdog, где дебаунс и
# однократность сделаны давно. Второй читатель токена в PowerShell был бы
# вторым числом на ту же вещь ровно там, где мы их вычищаем.
#
# PYTHONUTF8=1 — иначе cp1251-краш на эмодзи (jarvis-detached-bot-utf8).

param(
    [string]$Slug = 'yarina',
    # ПОРТ 8011 НАЗВАН В ТРЁХ МЕСТАХ, общей константы у python с PowerShell
    # быть не может. Правка одного обязана заставить найти остальные:
    #   1. scripts/run_panel_client.py DEFAULT_PORT   — на чём поднимается панель
    #   2. -Port здесь                                — на что смотрит гардиан
    #   3. scripts/ops_watchdog.py PANEL_CLIENT_PORT  — куда ходит проба
    # Четвёртое место (дефолт в регистраторе) убрано намеренно: задача зовёт
    # этот скрипт без -Port, то есть значение объявлено здесь ОДИН раз.
    [int]$Port = 8011,
    [int]$IntervalSeconds = 15,
    # Пауза после осознанного отказа старта (rc 1). 300 с, ОК владельца 20.08.
    [int]$BackoffSeconds = 300,
    # Столько отказов ПОДРЯД — и мы уходим на длинный интервал. Не стоп.
    [int]$MaxRefusals = 3,
    # Длинный интервал исчерпанных отказов: 1800 с (30 мин). Выход из состояния
    # АВТОМАТИЧЕСКИЙ — починили окружение, и в пределах получаса панель
    # поднялась сама, без рестарта задачи.
    [int]$LongRetrySeconds = 1800,
    # Параметризовано ради тестов (временный корень) — прод флаг не передаёт.
    [string]$Root = 'C:\jarvis',
    # Тестовый хук: только определить функции, не брать лок и не входить в
    # бесконечный цикл. Задача этот флаг не передаёт — поведение не меняется.
    [switch]$NoLoop
)

$ErrorActionPreference = 'Continue'
if (Test-Path $Root) { Set-Location $Root }
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

# Пути собираются ПО ЧАСТЯМ через Join-Path, а не литералами с разделителями.
# Причина названа фактом: экранная последовательность в литерале пути однажды
# раскрылась ЕЩЁ ПРИ ЗАПИСИ ФАЙЛА, путь стал несуществующим, и функция молча
# вернула пустоту (см. TAILSCALE_EXE в scripts/run_panel_client.py).
$stateDir  = Join-Path $Root 'state'
$stateLogs = Join-Path $stateDir 'logs'
$panelLogs = Join-Path $Root 'logs'
$lockDir   = Join-Path $stateDir 'locks'
$scripts   = Join-Path $Root 'scripts'
$py        = Join-Path (Join-Path (Join-Path $Root '.venv') 'Scripts') 'python.exe'
if (-not (Test-Path $py)) { $py = 'python' }
$runner    = Join-Path $scripts 'run_panel_client.py'

New-Item -ItemType Directory -Force -Path $stateLogs, $panelLogs, $lockDir | Out-Null

# ДВА РАЗНЫХ ЖУРНАЛА, И ЭТО НАМЕРЕННО (§2 п.2 и §5 п.5).
#   * подопечный пишет в logs/panel_<slug>.*.log — ТОТ ЖЕ файл, по которому
#     разбирали смерть 20.08 (§0), и туда же пишет живая панель сейчас. Разбор
#     аварии не должен начинаться с поиска, куда переехал лог;
#   * присматривающий пишет в state/logs/panel_client_guardian.stdout.log —
#     рядом с соседями-гардианами. Смешать их значит потерять границу между
#     «что сказала панель» и «что решил гардиан».
$lockFile = Join-Path $lockDir   ('panel_client_guardian_{0}.pid' -f $Slug)
$gOut     = Join-Path $stateLogs 'panel_client_guardian.stdout.log'
$pOut     = Join-Path $panelLogs ('panel_{0}.stdout.log' -f $Slug)
$pErr     = Join-Path $panelLogs ('panel_{0}.stderr.log' -f $Slug)

# ВОКАБУЛЯР ВЕРДИКТА — ЛИТЕРАЛАМИ ПО МЕСТУ, А НЕ ПЕРЕМЕННОЙ. Так же, как в
# `ops_watchdog.probe_panel_client`, где 'no_response' и 'http:%s' тоже стоят
# литералами. Причина не в лени: чистые функции ниже (Test-ShouldStartPanel,
# Step-RefusalState, Format-RefusalLine, Test-ShouldLogUnmeasurable) зовутся из pytest
# дот-сорсом. Опирайся они на переменную внешней области, любой вызов вне
# полного дот-сорса молча сравнивал бы вердикт с $null — то есть 'ok' перестал
# бы быть 'ok', и функция начала бы поднимать панель поверх ЖИВОЙ. Чистая
# функция, зависящая от чужой области, чистой не является.
#
#     ok | no_response | http:<код> | no_bind_address

function Write-G([string]$msg) {
    # Add-Content + Write-Host, а НЕ Tee-Object: Tee пропускает каждую строку
    # ЧЕРЕЗ конвейер, и функция, вызвавшая логгер, возвращала бы массив строк
    # вместо своего значения — `if (-not (Stop-...))` видел бы истинный массив
    # и никогда не срабатывал (разбор в bot_guardian_detached.ps1).
    #
    # -Encoding utf8 ЯВНО: без него Add-Content берёт системную ANSI (cp1251),
    # и причина отказа, записанная дословно, приезжает кракозябрами — то есть
    # ровно та строка, ради которой всё это и заведено.
    #
    # Слаг в КАЖДОЙ строке: журнал у гардианов один на всех клиентов, и без
    # имени клиента две панели писали бы в него неразличимо.
    $line = ('{0} | {1} | {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Slug, $msg)
    try { Add-Content -LiteralPath $gOut -Value $line -Encoding utf8 } catch { }
    Write-Host $line
}

# ── адрес: ОДНА функция на бинд и на пробу ──────────────────────────────────
function Get-PanelBind {
    <#
      Что известно об адресе панели: адрес тайнета, явно заданный адрес и то,
      что из них выбрал резолвер.

      Вычисляем НЕ здесь: зовём resolve_client_host вместе с tailnet_ip из
      scripts/run_panel_client.py — те самые функции, которыми панель выбирает
      адрес бинда. Своя копия (или зашитый 127.0.0.1) была бы вторым числом на
      одну вещь: панель биндится на адрес тайнета, НА ПЕТЛЕ ЕЁ НЕТ.

      Возвращаются ВСЕ ТРИ значения, а не один адрес: без tailnet_ip и без
      PANEL_CLIENT_HOST невозможно отличить «панель осознанно на петле» от
      «тайнет пропал, и резолвер отдал петлю фолбэком» — а это разница между
      законным измерением и убийством здоровой панели (§2.3).

      В коде для python нет НИ ОДНОЙ кавычки намеренно: PowerShell 5.1 теряет
      вложенные кавычки при передаче аргумента нативному exe, и строка молча
      приезжала бы битой. По той же причине пустое значение печатается как 0
      (сентинел): пустая строка и пропавшая строка в выводе неразличимы, а
      адресом 0 быть не может.
    #>
    $code = @(
        'import sys, os',
        'sys.path.insert(0, sys.argv[1])',
        'import run_panel_client as m',
        'ip = (m.tailnet_ip() or str()).strip()',
        'explicit = (os.environ.get(m.HOST_VAR) or str()).strip()',
        'h, p = m.resolve_client_host(None, ip=ip)',
        'print(ip if ip else 0)',
        'print(explicit if explicit else 0)',
        'print(h if h else 0)'
    ) -join '; '
    $result = [pscustomobject]@{ TailnetIp = ''; ExplicitHost = ''; BindHost = ''; Failed = $true }
    try {
        $out = @(& $py -c $code $scripts 2>$null)
    } catch {
        Write-G "не смог спросить адрес бинда: $($_.Exception.GetType().Name) $($_.Exception.Message)"
        return $result
    }
    if ($out.Count -lt 3) {
        Write-G "резолвер адреса вернул $($out.Count) строк(и) вместо 3 — адрес неизвестен"
        return $result
    }
    $clean = { param($v) $s = (($v) + '').Trim(); if ($s -eq '0') { '' } else { $s } }
    $result.TailnetIp    = (& $clean $out[0])
    $result.ExplicitHost = (& $clean $out[1])
    $result.BindHost     = (& $clean $out[2])
    $result.Failed       = $false
    return $result
}

function Test-PanelAddressMeasurable {
    <#
      Есть ли у нас адрес, по которому вообще ЗАКОННО судить о панели.

      Чистая функция: «панель не ответила» на неправильном адресе выглядит
      ровно как смерть, и цена ошибки здесь не ложный алерт, а taskkill
      владельца порта и перезапуск ЖИВОЙ панели.

        * адреса нет вовсе          -> нет (резолвер отказал; сегодня это 0.0.0.0)
        * адрес задан явно          -> ДА (петлю выбрали осознанно, значит там и мерить)
        * тайнета нет и явного нет  -> нет: петля пришла ФОЛБЭКОМ резолвера, а
                                       панель, поднятая при живом тайнете,
                                       слушает тайнетовый адрес (§2.3)
        * тайнет есть               -> ДА
    #>
    param(
        [string]$TailnetIp = '',
        [string]$ExplicitHost = '',
        [string]$BindHost = ''
    )
    if (-not $BindHost) { return $false }
    if ($ExplicitHost) { return $true }
    if (-not $TailnetIp) { return $false }
    return $true
}

# ── измерение: те же четыре слова, что у пробы ──────────────────────────────
function Get-PanelPortOwner {
    # PID'ы тех, кто СЛУШАЕТ порт. Единственный законный способ узнать, кого
    # именно снимать: адресуемся владельцу порта, а не похожей строке запуска.
    param([int]$PanelPort)
    try {
        $conns = @(Get-NetTCPConnection -LocalPort $PanelPort -State Listen -ErrorAction Stop)
    } catch {
        # 🔴 ЗАМЕРЕНО, А НЕ ПРЕДПОЛОЖЕНО. На СВОБОДНОМ порту этот командлет не
        # возвращает пустой список — он БРОСАЕТ CimJobException с категорией
        # ObjectNotFound (FQEID CmdletizationQuery_NotFound). То есть самый
        # обычный случай «панель мертва, порт свободен» приходил сюда как
        # ошибка, и общий catch писал бы в журнал строку «не смог перечислить
        # слушателей» КАЖДЫЕ 15 СЕКУНД — на совершенно штатном пути. Вечно
        # красная лампа перестаёт читаться, и настоящий сбой опроса утонул бы
        # в ней же.
        #
        # Поэтому «никто не слушает» отделено от «не смогли спросить»:
        # первое — тихий пустой список, второе — строка в журнале (DEV-18).
        if ("$($_.CategoryInfo.Category)" -eq 'ObjectNotFound') { return @() }
        Write-G "не смог перечислить слушателей порта $PanelPort ($($_.Exception.GetType().Name): $($_.FullyQualifiedErrorId))"
        return @()
    }
    return @($conns | ForEach-Object { [int]$_.OwningProcess } | Sort-Object -Unique)
}

function Test-PanelPort {
    param([int]$PanelPort)
    return ((Get-PanelPortOwner -PanelPort $PanelPort).Count -gt 0)
}

function Get-PanelHealthStatus {
    <#
      HTTP-код ручки /health, или 0, если ответа не было вовсе.

      Код, а не булево: «ответил 500» и «не ответил» — разные аварии (первая
      про код внутри живого процесса, вторая про процесс), и слепить их значит
      потерять ровно ту разницу, ради которой у пробы два разных reason.

      Ручка неаутентифицированная и не говорит НИЧЕГО о клиенте — гардиану
      ключ не нужен и не должен быть нужен.
    #>
    param([string]$PanelHost, [int]$PanelPort, [int]$TimeoutSec = 5)
    if (-not $PanelHost) { return 0 }
    $url = 'http://{0}:{1}/health' -f $PanelHost, $PanelPort
    try {
        $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec $TimeoutSec
        return [int]$r.StatusCode
    } catch {
        # В PS 5.1 ответ 4xx/5xx прилетает ИСКЛЮЧЕНИЕМ, а не значением. Без
        # этой ветки живая-но-больная панель была бы неотличима от мёртвой.
        $resp = $null
        try { $resp = $_.Exception.Response } catch { $resp = $null }
        if ($resp) {
            try { return [int]$resp.StatusCode } catch { return 0 }
        }
        return 0
    }
}

function Get-PanelReason {
    <#
      Вердикт о панели ОДНИМ ИЗ ЧЕТЫРЁХ СЛОВ вокабуляра пробы §3.

      Порядок проверок не случаен: сначала «есть ли законный адрес» (§2.3),
      и только потом порт и ответ. Обратный порядок дал бы `no_response` на
      адресе, о котором мы уже знаем, что он не тот.

      Порт слушают, а /health не ответил вовсе -> `no_response`: у пробы это
      значит ровно то же самое, и расходиться словами с ней нельзя.

      🔴 У $PanelPort НЕТ УМОЛЧАНИЯ-ПОРТА, И ЭТО НАМЕРЕННО. Здесь стояло
      умолчание с тем же портом — вторая копия числа в исполняемом коде
      рядом с параметром скрипта. Такое умолчание переживает правку параметра и разъезжается
      МОЛЧА: порт сменили в одном месте, а внутренняя функция продолжает
      выносить вердикт о старом. Порт обязан ПРИХОДИТЬ в функцию, а не иметь
      своё мнение.

      Ноль — это сентинел «не передали», а не порт. Проверка в теле, а не
      атрибут `Mandatory`: атрибут сделал бы функцию advanced, а у advanced
      появляются общие параметры и вместе с ними риск столкнуться именем на
      ПРИВЯЗКЕ — падение до тела, без следов, читается как «функция
      промолчала» (ловушка `-Db` против алиаса `-Debug`). Молчать нельзя и
      здесь: порт 0 дал бы `no_response` о панели, которой никто не мерил.
    #>
    param(
        [string]$TailnetIp = '', [string]$ExplicitHost = '', [string]$BindHost = '',
        [int]$PanelPort = 0
    )
    if ($PanelPort -le 0) {
        throw 'Get-PanelReason: -PanelPort обязателен (порт приходит извне, своего умолчания у функции нет)'
    }
    if (-not (Test-PanelAddressMeasurable -TailnetIp $TailnetIp -ExplicitHost $ExplicitHost -BindHost $BindHost)) {
        return 'no_bind_address'
    }
    if (-not (Test-PanelPort -PanelPort $PanelPort)) { return 'no_response' }
    $code = Get-PanelHealthStatus -PanelHost $BindHost -PanelPort $PanelPort
    if ($code -eq 200) { return 'ok' }
    if ($code -le 0)   { return 'no_response' }
    return ('http:{0}' -f $code)
}

# ── РЕШЕНИЕ: поднимать или нет ──────────────────────────────────────────────
function Test-ShouldStartPanel {
    <#
      Чистая функция: ни одного обращения к диску, сети или времени. Именно
      её и проверяют сторожа — «процесс как-то поднялся» не является
      доказательством того, что решение принято верно.

      Вход — ВЕРДИКТ ПРОБЫ, а не булево «жив/мёртв». Состояний три, и третье
      («не могу измерить») на булеве неотличимо от смерти В ПРИНЦИПЕ:

        ok              -> НЕ поднимать НИКОГДА, какова бы ни была история
                           отказов: поверх живого не поднимают;
        no_bind_address -> НЕ поднимать и НИЧЕГО НЕ УБИВАТЬ (§2.3). Адрес не
                           тот, панель может быть совершенно здорова;
        http:<код>      -> панель отвечает, но не 200: внутри плохо, поднимать;
        no_response     -> порт молчит: поднимать.

      Ожидание после отказов, границы ВКЛЮЧИТЕЛЬНЫЕ:
        отказов 0       -> немедленно;
        отказов 1..2    -> ждать BackoffSeconds   (299 с — нет, 300 с — да);
        отказов >= 3    -> ждать LongRetrySeconds (1799 с — нет, 1800 с — да).

      Ожидание применяется ко ВСЕМ поднимающим вердиктам, а не только к
      no_response: счётчик отказов говорит о прошлых ПОПЫТКАХ, а не о нынешнем
      диагнозе, и «зависла» не является поводом долбить чаще, чем «молчит».

      «ПРЕКРАТИТЬ НАВСЕГДА» ЗДЕСЬ НЕТ И БЫТЬ НЕ ДОЛЖНО (§2.2). Жёсткий стоп
      требует человека у машины; 16.08 гардиан вошёл в состояние DOWN и не
      вышел из него 13 часов 42 минуты. Длинный интервал даёт то же самое
      «перестать долбить», но оставляет автоматический выход: починили
      окружение — и в пределах получаса панель поднялась сама.

      Неизвестное слово трактуется как `no_response` (поднимать по правилам
      ожидания), а не как `ok`: молчаливое «всё хорошо» на непонятом вердикте
      — это выключенный сторож.

      Функция НАМЕРЕННО не является advanced (ни CmdletBinding, ни атрибутов
      Parameter/Validate): у advanced-функции появляются общие параметры, и имя
      вроде -Refusals рискует столкнуться с сокращением чужого имени на
      привязке — падение случается ДО тела функции, следов не оставляет и
      читается как «функция промолчала».
    #>
    param(
        [string]$Reason = 'no_response',
        [int]$Refusals = 0,
        [int]$SinceRefusalSec = [int]::MaxValue,
        [int]$MaxRefusals = 3,
        [int]$BackoffSeconds = 300,
        [int]$LongRetrySeconds = 1800
    )
    if ($Reason -eq 'ok') { return $false }
    if ($Reason -eq 'no_bind_address') { return $false }
    if ($Refusals -le 0) { return $true }
    $wait = if ($Refusals -ge $MaxRefusals) { $LongRetrySeconds } else { $BackoffSeconds }
    return ($SinceRefusalSec -ge $wait)
}

function Step-RefusalState {
    <#
      Единственный переход состояния отказов. Чистая функция: на входе старое
      состояние и ИСХОД ПОПЫТКИ, на выходе новое состояние и признак «сказать
      вслух».

      Исходы:
        ok           — панель поднялась: счётчик обнуляется, флаг алерта
                       СНИМАЕТСЯ. Снятие обязательно: иначе один давний отказ
                       навсегда выключил бы сигнал — ровно так законная правка
                       тумблера выключила сторожа worktree на 1669 циклов;
        refused      — rc 1, осознанный fail-closed: счётчик +1;
        unmeasurable — no_bind_address: попытки НЕ БЫЛО, поэтому не трогаем ни
                       счётчик, ни флаг. Тратить отказ на то, чего мы не
                       мерили, значит уводить панель в длинный интервал за
                       чужую вину — за пропавший тайнет.

      `Alert` истинно РОВНО НА ПЕРЕХОДЕ (счётчик достиг предела, а флаг ещё не
      поднят) и больше никогда, пока не было успеха. Повторяющееся сообщение
      перестают читать: измерено на 25 живых вопросах хука за 7 минут, из
      которых 22 подтверждения нажали не глядя.

      ⚠️ ЕДИНИЦА СЧЁТА — РЕАЛЬНАЯ ПОПЫТКА СТАРТА, А НЕ ЦИКЛ (§2.2). При паузе
      300 с и цикле 15 с между двумя отказами проходит двадцать циклов;
      счётчик, считающий циклы, вошёл бы в состояние за 45 секунд вместо 15
      минут. Поэтому зовущий обязан звать эту функцию только по факту попытки.
    #>
    param(
        [string]$Outcome = 'refused',
        [int]$Refusals = 0,
        [bool]$Alerted = $false,
        [int]$MaxRefusals = 3
    )
    $next = $Refusals
    $nextAlerted = $Alerted
    $alert = $false

    switch ($Outcome) {
        'ok' {
            $next = 0
            $nextAlerted = $false
        }
        'refused' {
            $next = $Refusals + 1
            if (($next -ge $MaxRefusals) -and (-not $Alerted)) {
                $alert = $true
                $nextAlerted = $true
            }
        }
        'unmeasurable' {
            # Намеренно ничего: попытки не было, судить не о чем.
        }
        default {
            # DEV-18: не глотать. Незнакомый исход, тихо не изменивший
            # состояние, — это гардиан, который «работает», но не считает.
            throw "Step-RefusalState: неизвестный Outcome '$Outcome' (ожидались ok|refused|unmeasurable)"
        }
    }

    return [pscustomobject]@{ Refusals = $next; Alerted = $nextAlerted; Alert = $alert }
}

function Format-RefusalLine {
    <#
      Строка журнала об отказе: номер попытки и причина ДОСЛОВНО.

      Причина приходит из вывода run_panel_client.py, а не пишется константой:
      через час по журналу должно быть видно, одна и та же это ошибка или
      разные (§2.2). Константа «панель не поднялась» выглядела бы одинаково
      для пропавшего ключа, для совпадения с ключом владельца и для
      незаданного TAMAPI_DB — то есть ровно там, где разница и нужна, её бы не
      было.

      Причина НЕ обрезается: обрезка режет хвост, а в хвосте стоит имя
      переменной, которую надо задать.

      Параметр зовётся `-Refusal`, а НЕ `-Reason`: `-Reason` — это вокабуляр
      пробы (ok / no_response / http:<код> / no_bind_address), и он живёт
      только в Test-ShouldStartPanel. Здесь — человеческий текст из stdout
      панели. Одно имя на два несовместимых понятия стояло тут круг, и первый
      же вызов с вердиктом вместо текста дал бы строку журнала, не называющую
      ни одной причины.
    #>
    param([string]$Refusal = '', [int]$Refusals = 0)
    $text = ($Refusal + '').Trim()
    if (-not $text) { $text = '(причина не прочиталась)' }
    return ('ОТКАЗ СТАРТА (rc 1), попытка {0}: {1}' -f $Refusals, $text)
}

function Test-ShouldLogUnmeasurable {
    <#
      Говорить ли вслух про состояние «не могу измерить» (§2.3).

      Чистая функция от ДВУХ состояний — прошлого цикла и нынешнего, — потому
      что предмет здесь не состояние, а ПЕРЕХОД:

        было нет, стало да  -> ПИШЕМ (вход: тайнет пропал);
        было да,  стало нет -> ПИШЕМ (выход: адрес снова известен);
        было да,  стало да  -> МОЛЧИМ;
        было нет, стало нет -> МОЛЧИМ.

      Третий случай и есть весь смысл функции. Требование §2.2 «строка на
      КАЖДОЙ попытке» сюда не распространяется: попыток здесь нет по
      определению — мы ничего не поднимаем и ничего не убиваем. Ежецикловая
      запись при интервале 15 с за ночь пропавшего тайнета дала бы 2880 строк
      и похоронила бы в себе всё остальное, включая настоящие отказы старта.

      Четвёртый случай тоже назван явно: в обычном цикле про адрес не пишется
      НИЧЕГО. Журнал, в котором каждая строка — про то, что всё нормально,
      перестают читать так же быстро, как повторяющийся алерт.

      Пара «вход + выход» обязательна целиком: одна только запись о входе
      делает «тайнет пропадал на ночь» неотличимым от «тайнета нет до сих
      пор», а это разные разборы.
    #>
    param(
        [bool]$WasUnmeasurable = $false,
        [bool]$IsUnmeasurable = $false
    )
    return ($WasUnmeasurable -ne $IsUnmeasurable)
}

# ── освобождение порта: строго по владельцу ─────────────────────────────────
function Stop-PanelPortOwner {
    param([int]$PanelPort, [int]$MaxWaitSec = 10)
    foreach ($owner in (Get-PanelPortOwner -PanelPort $PanelPort)) {
        # PID 0 и 4 — Idle и System: их не бывает владельцами нашего порта, но
        # цена ошибки такова, что проверка дешевле разбора.
        if ($owner -le 4) { continue }
        if ($owner -eq $PID) {
            Write-G "владелец порта $PanelPort — это Я (PID $PID); не трогаю"
            continue
        }
        try { & taskkill.exe /PID $owner /T /F *> $null }
        catch { Write-G "taskkill PID $owner не сработал ($($_.Exception.GetType().Name))" }
        Write-G "taskkill -> PID $owner (владелец порта $PanelPort, +дерево)"
    }
    $deadline = (Get-Date).AddSeconds($MaxWaitSec)
    while ((Test-PanelPort -PanelPort $PanelPort) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 300
    }
    return (-not (Test-PanelPort -PanelPort $PanelPort))
}

function Get-RefusalReason {
    <#
      Причина отказа ИЗ ВЫВОДА run_panel_client.py, дословно.

      Берутся строки-пункты («  * ...»), которые печатает сам отказ. Если их
      нет (упало иначе), берём последнюю непустую строку — тоже наблюдение, а
      не догадка.
    #>
    param([int]$MaxLines = 20)
    try {
        if (-not (Test-Path $pOut)) { return '(stdout панели пуст)' }
        $lines = @(Get-Content -LiteralPath $pOut -Tail $MaxLines -ErrorAction Stop |
                   ForEach-Object { ($_ + '').Trim() } |
                   Where-Object { $_ })
        if (-not $lines) { return '(stdout панели пуст)' }
        $bullets = @($lines | Where-Object { $_.StartsWith('*') })
        if ($bullets.Count -gt 0) {
            return (($bullets | ForEach-Object { $_.TrimStart('*').Trim() }) -join '; ')
        }
        return $lines[-1]
    } catch {
        return "(не смог прочитать $pOut : $($_.Exception.GetType().Name))"
    }
}

function Start-Panel {
    <#
      Поднять инстанс. Возвращает строку-исход, а не булево: 'alive',
      'refused' (осознанный fail-closed, rc 1), 'exited:<код>' и 'timeout'
      требуют РАЗНОЙ реакции, и склеивать их в один $false значит снова
      перепутать отказ с падением.

      ПОРТ ОДИН И ТОТ ЖЕ: в `--port` уезжает РОВНО $PanelPort — то самое
      значение, по которому вынесен вердикт и по которому освобождался порт.
      Иначе оставалось бы возможным «подняли на 8011, судим по 8012».
    #>
    param([string]$PanelHost, [int]$PanelPort, [string]$PanelSlug, [int]$ReadySec = 45)

    if (-not (Stop-PanelPortOwner -PanelPort $PanelPort)) {
        Write-G "порт $PanelPort всё ещё занят — НЕ поднимаю (повтор на следующем цикле)"
        return 'busy'
    }

    $runnerArgs = @($runner, '--slug', $PanelSlug, '--port', [string]$PanelPort)
    try {
        $p = Start-Process -FilePath $py -ArgumentList $runnerArgs -WorkingDirectory $Root `
            -WindowStyle Hidden -RedirectStandardOutput $pOut -RedirectStandardError $pErr -PassThru
    } catch {
        Write-G "запуск не удался: $($_.Exception.GetType().Name) $($_.Exception.Message)"
        return 'launch_failed'
    }
    # Прикосновение к .Handle: PowerShell не кэширует системный хэндл, и после
    # смерти процесса .ExitCode вернул бы $null — то есть код возврата, ради
    # которого мы и различаем отказ от падения, стал бы недоступен.
    try { $null = $p.Handle } catch { Write-G "не удалось закэшировать хэндл панели: $($_.Exception.GetType().Name)" }
    Write-G "поднял панель (PID $($p.Id)) -> $PanelHost`:$PanelPort"

    for ($i = 0; $i -lt $ReadySec; $i++) {
        Start-Sleep -Seconds 1
        if ($p.HasExited) {
            $code = $null
            try { $code = $p.ExitCode } catch { $code = $null }
            if ($code -eq 1) { return 'refused' }
            Write-G "панель вышла с кодом $code через ~${i}с; stdout: $(Get-RefusalReason)"
            return ('exited:{0}' -f $code)
        }
        if ((Test-PanelPort -PanelPort $PanelPort) -and
            ((Get-PanelHealthStatus -PanelHost $PanelHost -PanelPort $PanelPort) -eq 200)) {
            Write-G "панель отвечает 200 на /health через ~${i}с"
            return 'alive'
        }
    }
    Write-G "панель не ответила за ${ReadySec}с — повтор на следующем цикле"
    return 'timeout'
}

# ── single-instance: PID-лок ────────────────────────────────────────────────
# Пропускается под -NoLoop: сторожа дот-сорсят файл ради функций выше и не
# имеют права взять лок настоящего гардиана.
if (-not $NoLoop) {
    if (Test-Path $lockFile) {
        $old = (Get-Content -LiteralPath $lockFile -ErrorAction SilentlyContinue | Select-Object -First 1)
        if ($old) {
            try {
                $alive = Get-Process -Id ([int]$old) -ErrorAction SilentlyContinue
                # Сверяем И номер, И ЧТО ЭТО POWERSHELL. PID-файл переживает
                # kill, а номер ОС переиспользует: «PID существует» не значит
                # «гардиан жив» (ловушка 3 chatter-гардиана).
                $name = (($alive.ProcessName) + '').ToLower()
                if ($alive -and ($name -eq 'powershell' -or $name -eq 'pwsh')) {
                    Write-G "гардиан панели уже работает (PID $old) — выхожу"
                    return
                }
            } catch {
                Write-G "лок $lockFile нечитаем ($($_.Exception.GetType().Name)) — беру его себе"
            }
        }
    }
    $PID | Out-File -FilePath $lockFile -Encoding ascii -Force
    Write-G "гардиан стартовал (PID $PID), порт $Port, интервал ${IntervalSeconds}с, пауза после отказа ${BackoffSeconds}с, предел отказов $MaxRefusals, длинный интервал ${LongRetrySeconds}с"
}

if (-not $NoLoop) {
    $refusals = 0
    $alerted = $false
    $lastRefusalAt = $null
    $lastReason = ''

    while ($true) {
        $bind = Get-PanelBind
        $reason = Get-PanelReason -TailnetIp $bind.TailnetIp -ExplicitHost $bind.ExplicitHost `
            -BindHost $bind.BindHost -PanelPort $Port

        # Писать ли про адрес — решает ОДНА чистая функция, и она же держит
        # третий случай («было да, стало да» -> молчим), ради которого всё это
        # и заведено. Направление перехода выбирается ниже: вход и выход
        # обязаны быть оба, иначе «тайнет пропадал на ночь» неотличимо от
        # «тайнета нет до сих пор».
        $isUnmeasurable = ($reason -eq 'no_bind_address')
        $wasUnmeasurable = ($lastReason -eq 'no_bind_address')
        $sayAboutAddress = Test-ShouldLogUnmeasurable -WasUnmeasurable $wasUnmeasurable `
            -IsUnmeasurable $isUnmeasurable

        if ($sayAboutAddress -and -not $isUnmeasurable) {
            Write-G "no_bind_address СНЯТ: адрес бинда снова известен ($($bind.BindHost)), возобновляю измерение"
        }

        if ($isUnmeasurable) {
            # «НЕ МОГУ ИЗМЕРИТЬ», а не «мертва» (§2.3). Ничего не убиваем и
            # ничего не поднимаем: панель, поднятая при живом тайнете, СЕЙЧАС
            # слушает тайнетовый адрес, и снести владельца порта значило бы
            # убить здоровое по собственной слепоте. Кричит об этом красная
            # проба panel_client, а здесь — строка на ВХОД и на ВЫХОД, и
            # только: ежецикловая запись раз в 15 с дала бы за ночь 2880 строк
            # и похоронила бы всё остальное.
            if ($sayAboutAddress) {
                $why = if ($bind.Failed) { 'резолвер адреса не ответил' }
                       elseif (-not $bind.BindHost) { 'резолвер отказал в адресе' }
                       else { "тайнет недоступен, а $($bind.BindHost) — это фолбэк резолвера" }
                Write-G "no_bind_address: $why. НИЧЕГО НЕ ТРОГАЮ (не могу измерить != мертва)"
            }
            # Счётчик отказов не трогаем: попытки не было.
            $st = Step-RefusalState -Outcome 'unmeasurable' -Refusals $refusals -Alerted $alerted -MaxRefusals $MaxRefusals
            $refusals = $st.Refusals
            $alerted = $st.Alerted
            $lastReason = $reason
            Start-Sleep -Seconds $IntervalSeconds
            continue
        }

        if ($reason -eq 'ok') {
            if ($lastReason -ne 'ok') { Write-G "панель жива ($($bind.BindHost)`:$Port)" }
            $st = Step-RefusalState -Outcome 'ok' -Refusals $refusals -Alerted $alerted -MaxRefusals $MaxRefusals
            $refusals = $st.Refusals
            $alerted = $st.Alerted
            $lastRefusalAt = $null
            $lastReason = $reason
            Start-Sleep -Seconds $IntervalSeconds
            continue
        }

        if ($lastReason -ne $reason) { Write-G "вердикт: $reason ($($bind.BindHost)`:$Port)" }
        $lastReason = $reason

        $since = [int]::MaxValue
        if ($lastRefusalAt) {
            $elapsed = ((Get-Date) - $lastRefusalAt).TotalSeconds
            $since = if ($elapsed -ge [int]::MaxValue) { [int]::MaxValue } else { [int]$elapsed }
        }

        $should = Test-ShouldStartPanel -Reason $reason -Refusals $refusals -SinceRefusalSec $since `
            -MaxRefusals $MaxRefusals -BackoffSeconds $BackoffSeconds -LongRetrySeconds $LongRetrySeconds
        if (-not $should) {
            Start-Sleep -Seconds $IntervalSeconds
            continue
        }

        $outcome = Start-Panel -PanelHost $bind.BindHost -PanelPort $Port -PanelSlug $Slug

        if ($outcome -eq 'refused') {
            $lastRefusalAt = Get-Date
            $st = Step-RefusalState -Outcome 'refused' -Refusals $refusals -Alerted $alerted -MaxRefusals $MaxRefusals
            $refusals = $st.Refusals
            $alerted = $st.Alerted
            # СТРОКА НА КАЖДОЙ ПОПЫТКЕ, И С ПРИЧИНОЙ ИЗ ВЫВОДА ПАНЕЛИ.
            Write-G (Format-RefusalLine -Refusal (Get-RefusalReason) -Refusals $st.Refusals)
            if ($st.Alert) {
                # РОВНО ОДИН раз на вход в состояние, и только в ЖУРНАЛ:
                # телеграм — работа пробы §3 через существующий evaluate.
                Write-G "ИСЧЕРПАНЫ ОТКАЗЫ: $($st.Refusals) подряд. Перехожу на длинный интервал ${LongRetrySeconds}с и ПРОДОЛЖАЮ пытаться. Починка окружения поднимет панель САМА, рестарт задачи не нужен. Эта строка больше не повторится — состояние видно по красной пробе panel_client."
            }
        } elseif ($outcome -eq 'alive') {
            $st = Step-RefusalState -Outcome 'ok' -Refusals $refusals -Alerted $alerted -MaxRefusals $MaxRefusals
            $refusals = $st.Refusals
            $alerted = $st.Alerted
            $lastRefusalAt = $null
            $lastReason = 'ok'
        }

        Start-Sleep -Seconds $IntervalSeconds
    }
}
