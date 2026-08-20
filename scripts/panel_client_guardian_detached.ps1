# Присмотр за КЛИЕНТСКОЙ панелью (один клиент = один процесс = один порт),
# полностью отвязанный от интерактивной сессии. Запускается задачей
# JarvisPanelClientGuardian (S4U / RunLevel Highest, AtStartup + AtLogOn) — так
# же, как JarvisBackendGuardian / JarvisBotGuardian / JarvisChatterGuardian.
#
# Спека: docs/superpowers/specs/2026-08-20-client-panel-supervisor.md (вариант B).
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
# ТРИ ГРАБЛИ, ЗАКРЫТЫЕ ПО ПОСТРОЕНИЮ:
#   1. Освобождение порта адресуется ВЛАДЕЛЬЦУ ПОРТА (Get-NetTCPConnection ->
#      OwningProcess), и только ему. Поиск процесса по подстроке его строки
#      запуска здесь ЗАПРЕЩЁН: под такой поиск попадает панель ДРУГОГО клиента
#      и pytest, который её импортирует. Ровно этот класс убил боевого бота
#      18.08 (DEV-38) — скрипт из worktree снёс прод по глобальной подстроке.
#   2. Гардиан не находит самого себя: решение принимается по порту и по
#      HTTP-ответу, а не по строке запуска, поэтому строке-маркеру просто
#      неоткуда взяться (ловушка «запуск != упоминание»).
#   3. Отказ старта != падение. run_panel_client.py при кривом окружении
#      печатает «ОТКАЗ, инстанс не поднят» и возвращает rc 1 — это осознанный
#      fail-closed. Перезапускать его раз в 15 секунд значит крутить вечный
#      цикл и ПРЯТАТЬ причину. Здесь: пауза BackoffSeconds, причина в лог
#      дословно, MaxRefusals подряд — прекратить попытки и оставить панель
#      мёртвой ГРОМКО (десятая проба ops_watchdog закричит).
#
# PYTHONUTF8=1 — иначе cp1251-краш на эмодзи (jarvis-detached-bot-utf8).

param(
    [string]$Slug = 'yarina',
    [int]$Port = 8011,
    [int]$IntervalSeconds = 15,
    # Пауза после осознанного отказа старта (rc 1). 300 с, ОК владельца 20.08.
    [int]$BackoffSeconds = 300,
    # Столько отказов ПОДРЯД — и мы перестаём пытаться.
    [int]$MaxRefusals = 3,
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
$stateDir = Join-Path $Root 'state'
$logDir   = Join-Path $stateDir 'logs'
$lockDir  = Join-Path $stateDir 'locks'
$scripts  = Join-Path $Root 'scripts'
$py       = Join-Path (Join-Path (Join-Path $Root '.venv') 'Scripts') 'python.exe'
if (-not (Test-Path $py)) { $py = 'python' }
$runner   = Join-Path $scripts 'run_panel_client.py'

New-Item -ItemType Directory -Force -Path $logDir, $lockDir | Out-Null

$lockFile = Join-Path $lockDir ('panel_client_guardian_{0}.pid' -f $Slug)
$gOut     = Join-Path $logDir  ('panel_client_guardian_{0}.stdout.log' -f $Slug)
$pOut     = Join-Path $logDir  ('panel_{0}.stdout.log' -f $Slug)
$pErr     = Join-Path $logDir  ('panel_{0}.stderr.log' -f $Slug)

function Write-G([string]$msg) {
    # Add-Content + Write-Host, а НЕ Tee-Object: Tee пропускает каждую строку
    # ЧЕРЕЗ конвейер, и функция, вызвавшая логгер, возвращала бы массив строк
    # вместо своего значения — `if (-not (Stop-...))` видел бы истинный массив
    # и никогда не срабатывал (разбор в bot_guardian_detached.ps1).
    #
    # -Encoding utf8 ЯВНО: без него Add-Content берёт системную ANSI (cp1251),
    # и причина отказа, записанная дословно, приезжает кракозябрами — то есть
    # ровно та строка, ради которой всё это и заведено.
    $line = ('{0} | {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg)
    try { Add-Content -LiteralPath $gOut -Value $line -Encoding utf8 } catch { }
    Write-Host $line
}

# ── адрес: ОДНА функция на бинд и на пробу ──────────────────────────────────
function Get-PanelHost {
    <#
      Адрес, на котором панель поднимается И на котором её надо спрашивать.

      Вычисляем его НЕ здесь: зовём resolve_client_host вместе с tailnet_ip из
      scripts/run_panel_client.py — те самые функции, которыми панель выбирает
      адрес бинда. Своя копия (или зашитый 127.0.0.1) была бы вторым числом на
      одну вещь: панель биндится на адрес тайнета, НА ПЕТЛЕ ЕЁ НЕТ, и проверка
      по петле была бы вечно красной на здоровой панели — то есть гардиан
      поднимал бы живое поверх живого.

      В коде для python нет НИ ОДНОЙ кавычки намеренно: PowerShell 5.1 теряет
      вложенные кавычки при передаче аргумента нативному exe, и строка молча
      приезжала бы битой.
    #>
    $code = @(
        'import sys',
        'sys.path.insert(0, sys.argv[1])',
        'import run_panel_client as m',
        'h, p = m.resolve_client_host(None, ip=m.tailnet_ip())',
        'print(h if h else str())'
    ) -join '; '
    try {
        $out = & $py -c $code $scripts 2>$null
    } catch {
        Write-G "не смог спросить адрес бинда: $($_.Exception.GetType().Name) $($_.Exception.Message)"
        return ''
    }
    $value = (($out | Select-Object -First 1) + '').Trim()
    return $value
}

function Resolve-PanelHost {
    # Кэш на цикл: tailscale.exe спрашивать 4 раза в минуту незачем. Кэш
    # сбрасывается там, где адрес мог протухнуть, — перед подъёмом и после
    # неудачной проверки.
    param([switch]$Refresh)
    if ($Refresh -or -not $script:PanelHost) {
        $script:PanelHost = Get-PanelHost
    }
    return $script:PanelHost
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

      Правила, и все три обязаны быть здесь, а не в теле цикла:
        * жива                       -> НЕ поднимать (не поверх живого);
        * мертва, отказов нет        -> поднимать;
        * был отказ, пауза не вышла  -> НЕ поднимать (иначе долбёжка раз в 15 с
                                        засыпает лог и прячет причину);
        * отказов >= MaxRefusals     -> НЕ поднимать больше НИКОГДА в этом
                                        экземпляре. Вечный цикл перезапуска
                                        хуже честного «не поднимается, вот
                                        почему»: панель всё равно мертва, но
                                        причина утоплена в шуме.

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
        [double]$BackoffSeconds = 300
    )
    if ($Alive) { return $false }
    if ($Refusals -ge $MaxRefusals) { return $false }
    if ($Refusals -gt 0 -and $SecondsSinceRefusal -lt $BackoffSeconds) { return $false }
    return $true
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
    # Причина отказа ДОСЛОВНО. run_panel_client.py печатает её в stdout
    # («ОТКАЗ, инстанс не поднят: * ...»), и пересказывать её своими словами
    # значит потерять единственное, ради чего вся эта ветка существует.
    param([int]$MaxLines = 12)
    try {
        if (-not (Test-Path $pOut)) { return '(stdout панели пуст)' }
        $lines = @(Get-Content -LiteralPath $pOut -Tail $MaxLines -ErrorAction Stop)
        if (-not $lines) { return '(stdout панели пуст)' }
        return ($lines -join ' / ')
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
    Write-G "поднял панель $PanelSlug (PID $($p.Id)) -> $PanelHost`:$PanelPort"

    for ($i = 0; $i -lt $ReadySec; $i++) {
        Start-Sleep -Seconds 1
        if ($p.HasExited) {
            $code = $null
            try { $code = $p.ExitCode } catch { $code = $null }
            if ($code -eq 1) {
                Write-G "ОТКАЗ СТАРТА (rc 1) — причина дословно: $(Get-RefusalReason)"
                return 'refused'
            }
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
                    Write-G "гардиан панели $Slug уже работает (PID $old) — выхожу"
                    return
                }
            } catch {
                Write-G "лок $lockFile нечитаем ($($_.Exception.GetType().Name)) — беру его себе"
            }
        }
    }
    $PID | Out-File -FilePath $lockFile -Encoding ascii -Force
    Write-G "гардиан панели $Slug стартовал (PID $PID), порт $Port, интервал ${IntervalSeconds}с, пауза после отказа ${BackoffSeconds}с, предел отказов $MaxRefusals"
}

if (-not $NoLoop) {
    $script:PanelHost = ''
    $refusals = 0
    $lastRefusalAt = $null
    $lastState = ''
    $gaveUpAnnounced = $false

    while ($true) {
        $panelHost = Resolve-PanelHost
        if (-not $panelHost) {
            # «Не смогли спросить адрес» — это НЕ «панель мертва». Решение о
            # подъёме на невычисленном адресе снесло бы живую панель по порту
            # и подняло бы её заново без всякой причины.
            Write-G 'адрес бинда не вычислился — цикл пропущен (это не диагноз панели)'
            Start-Sleep -Seconds $IntervalSeconds
            continue
        }

        $alive = Test-Panel -PanelHost $panelHost -PanelPort $Port
        if ($alive) {
            if ($lastState -ne 'alive') { Write-G "панель жива ($panelHost`:$Port)"; $lastState = 'alive' }
            # Живая панель обнуляет счётчик отказов: окружение починили, и
            # держать её на прежнем приговоре значило бы наказывать за прошлое.
            $refusals = 0
            $lastRefusalAt = $null
            $gaveUpAnnounced = $false
            Start-Sleep -Seconds $IntervalSeconds
            continue
        }

        if ($lastState -ne 'dead') { Write-G "панель НЕ отвечает ($panelHost`:$Port)"; $lastState = 'dead' }

        $sinceRefusal = 1e9
        if ($lastRefusalAt) { $sinceRefusal = ((Get-Date) - $lastRefusalAt).TotalSeconds }

        $should = Test-ShouldStartPanel -Alive $alive -Refusals $refusals `
            -SecondsSinceRefusal $sinceRefusal -MaxRefusals $MaxRefusals `
            -BackoffSeconds $BackoffSeconds

        if (-not $should) {
            if ($refusals -ge $MaxRefusals) {
                if (-not $gaveUpAnnounced) {
                    Write-G "$MaxRefusals ОТКАЗА СТАРТА ПОДРЯД — ПРЕКРАЩАЮ ПОПЫТКИ. Панель остаётся мёртвой намеренно; причина выше дословно. Починить окружение и перезапустить задачу JarvisPanelClientGuardian."
                    $gaveUpAnnounced = $true
                }
            } else {
                Write-G ("пауза после отказа: прошло {0:N0}с из ${BackoffSeconds}с (отказов $refusals из $MaxRefusals)" -f $sinceRefusal)
            }
            Start-Sleep -Seconds $IntervalSeconds
            continue
        }

        # Адрес перечитываем ПЕРЕД подъёмом: между циклами тайнет мог сменить
        # адрес, и поднимать панель на протухшем значении незачем.
        $panelHost = Resolve-PanelHost -Refresh
        $outcome = Start-Panel -PanelHost $panelHost -PanelPort $Port -PanelSlug $Slug
        if ($outcome -eq 'refused') {
            $refusals++
            $lastRefusalAt = Get-Date
            Write-G "отказ $refusals из $MaxRefusals; следующая попытка не раньше чем через ${BackoffSeconds}с"
        } elseif ($outcome -eq 'alive') {
            $lastState = 'alive'
            $refusals = 0
            $lastRefusalAt = $null
            $gaveUpAnnounced = $false
        }

        Start-Sleep -Seconds $IntervalSeconds
    }
}
