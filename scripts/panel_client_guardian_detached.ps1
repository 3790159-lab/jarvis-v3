# Присмотр за КЛИЕНТСКОЙ панелью (один клиент = один процесс = один порт),
# полностью отвязанный от интерактивной сессии. Запускается задачей
# JarvisPanelClientGuardian (S4U / RunLevel Highest, AtStartup + AtLogOn) — так
# же, как JarvisBackendGuardian / JarvisBotGuardian / JarvisChatterGuardian.
#
# Спека: docs/superpowers/specs/2026-08-20-client-panel-supervisor.md (вариант B,
# с §2.2 «исчерпанные отказы» и §2.3 «пропал тайнет != панель умерла»).
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
#      Алерт при этом РОВНО ОДИН — на вход в состояние: повторяющееся
#      сообщение перестают читать (25 живых вопросов хука за 7 минут).
#   4. Пропал тайнет != панель умерла (§2.3). resolve_client_host при
#      недоступном tailscale молча падает на петлю, живая панель при этом
#      слушает СТАРЫЙ тайнетовый адрес — и «не отвечает» означало бы убийство
#      здоровой панели. Такой случай зовётся no_bind_address, и гардиан на нём
#      НЕ ДЕЛАЕТ НИЧЕГО.
#
# PYTHONUTF8=1 — иначе cp1251-краш на эмодзи (jarvis-detached-bot-utf8).

param(
    [string]$Slug = 'yarina',
    # 🔢 ПОРТ 8011 НАЗВАН В ЧЕТЫРЁХ МЕСТАХ, общей константы у python с
    # PowerShell быть не может. Правка одного обязана заставить найти остальные:
    #   1. scripts/run_panel_client.py DEFAULT_PORT          — на чём поднимается панель
    #   2. -Port здесь                                       — на что смотрит гардиан
    #   3. scripts/ops_watchdog.py PANEL_CLIENT_PORT         — куда ходит проба
    #   4. scripts/register_panel_client_guardian.ps1 $Port  — что уезжает в задачу
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

# ДВА РАЗНЫХ ЖУРНАЛА, И ЭТО НАМЕРЕННО (§5 п.5).
#   * подопечный пишет в logs/panel_<slug>.*.log — ТОТ ЖЕ файл, по которому
#     разбирали смерть 20.08 (§0), и туда же пишет живая панель сейчас. Разбор
#     аварии не должен начинаться с поиска, куда переехал лог;
#   * присматривающий пишет в state/logs/panel_client_guardian.stdout.log —
#     рядом с соседями-гардианами. Смешать их значит потерять границу между
#     «что сказала панель» и «что решил гардиан».
$lockFile = Join-Path $lockDir  ('panel_client_guardian_{0}.pid' -f $Slug)
$gOut     = Join-Path $stateLogs 'panel_client_guardian.stdout.log'
$pOut     = Join-Path $panelLogs ('panel_{0}.stdout.log' -f $Slug)
$pErr     = Join-Path $panelLogs ('panel_{0}.stderr.log' -f $Slug)

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

      Чистая функция — её и проверяют сторожа: «панель не ответила» на
      неправильном адресе выглядит ровно как смерть, и цена ошибки здесь не
      ложный алерт, а taskkill владельца порта и перезапуск ЖИВОЙ панели.

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

# ── живость: порт И HTTP, оба обязательны ───────────────────────────────────
function Get-PanelPortOwner {
    # PID'ы тех, кто СЛУШАЕТ порт. Единственный законный способ узнать, кого
    # именно снимать: адресуемся владельцу порта, а не похожей строке запуска.
    param([int]$PanelPort)
    try {
        $conns = @(Get-NetTCPConnection -LocalPort $PanelPort -State Listen -ErrorAction Stop)
    } catch {
        # Пусто и «не смогли спросить» — разные вещи; вторую называем вслух,
        # иначе решение о подъёме принимается на невидимой ошибке (DEV-18).
        Write-G "не смог перечислить слушателей порта $PanelPort ($($_.Exception.GetType().Name))"
        return @()
    }
    return @($conns | ForEach-Object { [int]$_.OwningProcess } | Sort-Object -Unique)
}

function Test-PanelPort {
    param([int]$PanelPort)
    return ((Get-PanelPortOwner -PanelPort $PanelPort).Count -gt 0)
}

function Test-PanelHealth {
    # 200 на /health. Ручка неаутентифицированная и не говорит НИЧЕГО о
    # клиенте — гардиану ключ не нужен и не должен быть нужен.
    param([string]$PanelHost, [int]$PanelPort, [int]$TimeoutSec = 5)
    if (-not $PanelHost) { return $false }
    $url = 'http://{0}:{1}/health' -f $PanelHost, $PanelPort
    try {
        $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec $TimeoutSec
        return ([int]$r.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Test-Panel {
    # Порт И ответ. Только процесс — НЕДОСТАТОЧНО: зависший uvicorn держит порт
    # и молчит, а для планировщика он при этом совершенно здоров.
    param([string]$PanelHost, [int]$PanelPort)
    if (-not (Test-PanelPort -PanelPort $PanelPort)) { return $false }
    return (Test-PanelHealth -PanelHost $PanelHost -PanelPort $PanelPort)
}

# ── РЕШЕНИЕ: поднимать или нет ──────────────────────────────────────────────
function Test-ShouldStartPanel {
    <#
      Чистая функция: ни одного обращения к диску, сети или времени. Именно
      её и проверяют сторожа — «процесс как-то поднялся» не является
      доказательством того, что решение принято верно.

      Правила, и все они обязаны быть здесь, а не в теле цикла:
        * жива                        -> НЕ поднимать (не поверх живого);
        * мертва, отказов нет         -> поднимать немедленно;
        * был отказ, отказов < предела-> ждать BackoffSeconds;
        * отказов >= предела          -> ждать LongRetrySeconds.

      🔴 «ПРЕКРАТИТЬ НАВСЕГДА» ЗДЕСЬ НЕТ И БЫТЬ НЕ ДОЛЖНО (§2.2). Жёсткий стоп
      требует человека у машины; 16.08 гардиан вошёл в состояние DOWN и не
      вышел из него 13 часов 42 минуты. Длинный интервал даёт то же самое
      «перестать долбить», но оставляет автоматический выход: починили
      окружение — и в пределах получаса панель поднялась сама.

      Функция НАМЕРЕННО не является advanced (ни CmdletBinding, ни атрибутов
      Parameter): у advanced-функции появляются общие параметры, и имя вроде
      -Refusals рискует столкнуться с сокращением чужого имени на привязке —
      падение случается ДО тела функции, следов не оставляет и читается как
      «функция промолчала».
    #>
    param(
        [bool]$Alive = $false,
        [int]$Refusals = 0,
        [double]$SecondsSinceRefusal = 1e9,
        [int]$MaxRefusals = 3,
        [double]$BackoffSeconds = 300,
        [double]$LongRetrySeconds = 1800
    )
    if ($Alive) { return $false }
    if ($Refusals -le 0) { return $true }
    $wait = if ($Refusals -ge $MaxRefusals) { $LongRetrySeconds } else { $BackoffSeconds }
    return ($SecondsSinceRefusal -ge $wait)
}

function Test-ShouldAlertExhausted {
    <#
      Алерт об исчерпанных отказах — РОВНО ОДИН, на ВХОД в состояние.

      Повторяющееся сообщение перестают читать: это уже измерено на 25 живых
      вопросах хука за 7 минут — 22 подтверждения были нажаты не глядя. Пока
      состояние держится, о нём говорит постоянно красная проба panel_client,
      а не второй, третий и сотый одинаковый крик.

      `AlreadyAlerted` — флаг СОСТОЯНИЯ, а не «алертили когда-то за всю жизнь
      процесса»: успешный подъём его снимает, и следующий вход в состояние
      обязан алертить снова. Иначе один давний отказ навсегда выключил бы
      сигнал — ровно тем способом, каким законная правка тумблера выключила
      сторожа worktree на 1669 циклов.
    #>
    param(
        [int]$Refusals = 0,
        [int]$MaxRefusals = 3,
        [bool]$AlreadyAlerted = $false
    )
    if ($AlreadyAlerted) { return $false }
    return ($Refusals -ge $MaxRefusals)
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
      КОРОТКАЯ строка с причиной отказа, взятая ИЗ ВЫВОДА run_panel_client.py.

      Не константа и не пересказ: через час по журналу должно быть видно, одна
      и та же это ошибка или разные (§2.2). Константа «панель не поднялась»
      выглядела бы одинаково для пропавшего ключа, для совпадения с ключом
      владельца и для незаданного TAMAPI_DB — то есть ровно там, где разница и
      нужна, её бы не было.

      Берутся строки-пункты («  * ...»), которые печатает сам отказ. Если их
      нет (упало иначе), берём последнюю непустую строку — тоже наблюдение, а
      не догадка.
    #>
    param([int]$MaxLines = 20, [int]$MaxChars = 300)
    try {
        if (-not (Test-Path $pOut)) { return '(stdout панели пуст)' }
        $lines = @(Get-Content -LiteralPath $pOut -Tail $MaxLines -ErrorAction Stop |
                   ForEach-Object { ($_ + '').Trim() } |
                   Where-Object { $_ })
        if (-not $lines) { return '(stdout панели пуст)' }
        $bullets = @($lines | Where-Object { $_.StartsWith('*') })
        $text = if ($bullets.Count -gt 0) {
            ($bullets | ForEach-Object { $_.TrimStart('*').Trim() }) -join '; '
        } else {
            $lines[-1]
        }
        if ($text.Length -gt $MaxChars) { $text = $text.Substring(0, $MaxChars) + '...' }
        return $text
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
        if (Test-Panel -PanelHost $PanelHost -PanelPort $PanelPort) {
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
    $lastRefusalAt = $null
    $lastState = ''
    $exhaustedAlerted = $false

    while ($true) {
        $bind = Get-PanelBind
        $measurable = Test-PanelAddressMeasurable -TailnetIp $bind.TailnetIp `
            -ExplicitHost $bind.ExplicitHost -BindHost $bind.BindHost

        if (-not $measurable) {
            # 🔴 no_bind_address = «НЕ МОГУ ИЗМЕРИТЬ», а не «мертва» (§2.3).
            # Ничего не убиваем и ничего не поднимаем: панель, поднятая при
            # живом тайнете, СЕЙЧАС слушает тайнетовый адрес, и снести
            # владельца порта значило бы убить здоровое по собственной
            # слепоте. Молчать тоже нельзя — об этом кричит красная проба
            # panel_client, а здесь остаётся строка в журнале.
            $why = if ($bind.Failed) { 'резолвер адреса не ответил' }
                   elseif (-not $bind.BindHost) { 'резолвер отказал в адресе' }
                   else { "тайнет недоступен, а $($bind.BindHost) — это фолбэк резолвера" }
            if ($lastState -ne 'no_bind') {
                Write-G "no_bind_address: $why. НИЧЕГО НЕ ТРОГАЮ (не могу измерить != мертва)"
                $lastState = 'no_bind'
            }
            Start-Sleep -Seconds $IntervalSeconds
            continue
        }

        $panelHost = $bind.BindHost
        $alive = Test-Panel -PanelHost $panelHost -PanelPort $Port

        if ($alive) {
            if ($lastState -ne 'alive') { Write-G "панель жива ($panelHost`:$Port)"; $lastState = 'alive' }
            # Живая панель снимает состояние целиком: окружение починили, и
            # держать её на прежнем приговоре значило бы наказывать за прошлое.
            # Снятый флаг алерта важен отдельно — следующий вход в состояние
            # обязан закричать снова.
            $refusals = 0
            $lastRefusalAt = $null
            $exhaustedAlerted = $false
            Start-Sleep -Seconds $IntervalSeconds
            continue
        }

        if ($lastState -ne 'dead') { Write-G "панель НЕ отвечает ($panelHost`:$Port)"; $lastState = 'dead' }

        $sinceRefusal = 1e9
        if ($lastRefusalAt) { $sinceRefusal = ((Get-Date) - $lastRefusalAt).TotalSeconds }

        $should = Test-ShouldStartPanel -Alive $alive -Refusals $refusals `
            -SecondsSinceRefusal $sinceRefusal -MaxRefusals $MaxRefusals `
            -BackoffSeconds $BackoffSeconds -LongRetrySeconds $LongRetrySeconds

        if (-not $should) {
            Start-Sleep -Seconds $IntervalSeconds
            continue
        }

        $outcome = Start-Panel -PanelHost $panelHost -PanelPort $Port -PanelSlug $Slug

        if ($outcome -eq 'refused') {
            $refusals++
            $lastRefusalAt = Get-Date
            # СТРОКА НА КАЖДОЙ ПОПЫТКЕ, И С ПРИЧИНОЙ ИЗ ВЫВОДА ПАНЕЛИ: через
            # час по журналу видно, одна и та же это ошибка или разные.
            $wait = if ($refusals -ge $MaxRefusals) { $LongRetrySeconds } else { $BackoffSeconds }
            Write-G "ОТКАЗ СТАРТА (rc 1), попытка ${refusals}: $(Get-RefusalReason) | следующая не раньше чем через ${wait}с"

            if (Test-ShouldAlertExhausted -Refusals $refusals -MaxRefusals $MaxRefusals -AlreadyAlerted $exhaustedAlerted) {
                # РОВНО ОДИН раз на вход в состояние. Дальше молчим: о том, что
                # состояние держится, говорит постоянно красная проба
                # panel_client, а не одинаковый крик каждые полчаса.
                Write-G "🚨 ИСЧЕРПАНЫ ОТКАЗЫ: $refusals подряд. Перехожу на длинный интервал ${LongRetrySeconds}с и продолжаю пытаться. Причина выше дословно; починка окружения поднимет панель САМА, рестарт задачи не нужен. Это сообщение больше не повторится — следи за пробой panel_client."
                $exhaustedAlerted = $true
            }
        } elseif ($outcome -eq 'alive') {
            $lastState = 'alive'
            $refusals = 0
            $lastRefusalAt = $null
            $exhaustedAlerted = $false
        }

        Start-Sleep -Seconds $IntervalSeconds
    }
}
