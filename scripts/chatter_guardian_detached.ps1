# Keeps the chatter Telethon userbot (chatter.telethon_run) alive, fully detached
# from any interactive/SSH session. Invoked by the JarvisChatterGuardian scheduled
# task (S4U / RunLevel Highest, like JarvisBotGuardian) so it survives logoff, SSH
# drops, the starting session, and a reboot (AtStartup) WITHOUT an interactive logon.
#
# Design mirrors bot_guardian_detached.ps1:
#   * Single-instance: a PID lockfile stops a second guardian.
#   * Liveness via the runner's own heartbeat file (state\chatter_heartbeat.txt,
#     rewritten every 30s) AND process existence — catches a hung runner, not just
#     a dead PID.
#   * Runner single-instance: Stop-OldRunner taskkills any stale/hung runner
#     (cmdline-matched, whole tree) before launch, so we never end up with the
#     6-instances-fighting-over-the-session failure the operator hit by hand.
#   * Clean start: the runner self-loads .env (TELEGRAM_API_ID/HASH, ANTHROPIC_API_KEY)
#     via its own .env fallback, so NO secrets are injected here.
#   * On DOWN: fire chatter_watch_check.py (stdlib-only) to TG-alert the operator.
#   * PYTHONUTF8=1 prevents the cp1251 emoji crash (memory jarvis-detached-bot-utf8).

param(
    [int]$IntervalSeconds = 30,
    [int]$HeartbeatMaxAgeSec = 180,   # tolerate a one-off GC/CPU stall (debounce is the 2nd line)
    [int]$DebounceFailures = 3,       # relaunch only after N consecutive failed checks
    [string]$Root = 'C:\jarvis',
    [switch]$NoLoop                    # test hook: define functions only, no lock, no loop
)

$ErrorActionPreference = 'Continue'
Set-Location $Root
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$py        = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }
$logDir    = Join-Path $Root 'logs'
$stateDir  = Join-Path $Root 'state'
$lockDir   = Join-Path $Root 'state\locks'
New-Item -ItemType Directory -Force -Path $logDir, $stateDir, $lockDir | Out-Null
$lockFile  = Join-Path $lockDir 'chatter_guardian.pid'
$gOut      = Join-Path $logDir 'chatter_guardian.stdout.log'
# Start-Process REQUIRES distinct files for stdout and stderr (pointing both at
# one file throws "file name ... is the same" and the launch silently fails —
# the exact cold-start bug caught on first real guardian relaunch). The runner's
# Python logging (IN/OUT/catch-up/telethon) goes to STDERR, so $rErr is the
# primary operational log (== the manual `2>&1` file); $rOut only gets the tiny
# startup print.
$gHbFile   = Join-Path $stateDir 'chatter_guardian_heartbeat.txt'
$watchCheck = Join-Path $Root 'scripts\chatter_watch_check.py'
$stateFile = Join-Path $stateDir 'chatter_clients.json'

# --- SEMIDEMO OVERRIDE УДАЛЁН -----------------------------------------------
# Здесь был блок, зашивавший ОДНОГО клиента (volska) в сам скрипт по наличию
# state\chatter_semidemo_volska.flag. Источник правды теперь
# chatter/clients/registry.yaml, а состав клиентов приходит планом из
# chatter.registry_cli. Подключение клиента снова НЕ является правкой этого
# скрипта — как и было задумано (онбординг-дырка №3).

function Get-ClientPaths {
    # Свои файлы на каждого клиента. Общий heartbeat сделал бы супервизор
    # слепым (свежесть одного читалась бы как жизнь всех), общий лог —
    # затирал бы историю соседа.
    #
    # stdout и stderr РАЗНЫЕ файлы: Start-Process с одинаковыми путями кидает
    # "file name ... is the same" и запуск молча проваливается (грабля первого
    # боевого холодного старта). Логи Python (IN/OUT/catch-up/telethon) идут в
    # STDERR, поэтому .log — основной операционный файл, а .stdout.log ловит
    # только маленький стартовый print.
    param([Parameter(Mandatory)][string]$Slug)
    [pscustomobject]@{
        Err  = Join-Path $logDir   "chatter_$Slug.log"
        Out  = Join-Path $logDir   "chatter_$Slug.stdout.log"
        Hb   = Join-Path $stateDir "chatter_heartbeat_$Slug.txt"
        Lock = Join-Path $lockDir  "chatter_runner_$Slug.pid"
    }
}

# --- DEMO OVERRIDE (yarina) УДАЛЁН ПРИ СЛИЯНИИ ------------------------------
# Блок приехал из транка (16b54aeb) уже ПОСЛЕ того, как ветка удалила соседний
# SEMIDEMO-блок, поэтому git слил оба без конфликта — и получилась мина:
#   * `Test-Path $semidemoFlag` ссылался на переменную, определение которой
#     ветка удалила (в PowerShell без StrictMode это $null → Test-Path бросает);
#   * $rErr/$rOut были мёртвыми записями: раннер запускается через
#     Get-ClientPaths -Slug, эти переменные больше никто не читает;
#   * env-пины CHATTER_PERSONAS/TELETHON_SESSION/CHATTER_DB подменяли состав
#     в обход реестра, то есть били по самому инварианту арки.
# Ярина возвращается ОТДЕЛЬНОЙ записью реестра со своим аккаунтом и своей
# сессией (9b), а не подменой состава на аккаунте Ольги.

# --- СЛОТ ОБЯЗАТЕЛЬСТВ (арка «б», принята Д-10 2026-07-24) -------------------
# Слот в проде. Флаг остаётся рубильником отката: убрать строку + рестарт таска
# → §8-строка молчит, блок в промпт не инъектится (byte-identical к до-арке).
# env фиксируется на СТАРТЕ раннера (читается каждый ход, но из окружения
# процесса) → правка этой строки требует рестарта ТАСКА гардиана, не раннера.
#
# ⚠️ CHATTER_PROMPT_DUMP здесь НЕ ставить: пишет ПОЛНЫЙ промпт (профиль +
# переписка лида = ПДн) в logs/prompt_dump.log, ~30-40КБ/ход. Только на время
# приёмки, вручную, с удалением дампа после — см. docs/chatter/DRILL_D10_OBLIGATIONS.md.
$env:CHATTER_OBLIGATIONS_SLOT = '1'

# --- КЭШ КЛАССИФИКАТОРА (арка arc/classifier-cache, приёмка 2026-07-25) ------
# Раскладка A1: стабильный префикс под cache_control-breakpoint, профиль и блок
# обязательств — вторым (некэшируемым) system-блоком. Убрать строку + рестарт
# ТАСКА → промпт байт-в-байт как до арки (ветка отката, тест держит).
# Пока строки нет, классификатор пишет ~7.8К ток кэша КАЖДЫЙ ход и не читает
# его ни разу = 83% стоимости хода.
$env:CHATTER_CLASSIFIER_CACHE = '1'

function Write-G([string]$msg) {
    # Add-Content + Write-Host (NOT Tee-Object): keep a side-effect-free return so
    # callers using `if (-not (Stop-OldRunner))` see a real boolean, not a log array.
    $line = ('{0} | {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg)
    # UTF-8 ЯВНО. Без -Encoding Add-Content берёт системную ANSI (здесь cp1251),
    # и строка «состав: … db=…» — единственная, где лог прямо называет активную
    # базу, — приезжала в панель кракозябрами. Читатель (jarvis_farm.read_tail)
    # чинит УЖЕ написанное построчным фолбэком utf-8 → cp1251; эта строка
    # убирает причину, чтобы фолбэк не был единственным рабочим путём.
    #
    # ⚠️ Смешанный файл после этой правки — норма, и это безопасно ИМЕННО
    # потому, что фолбэк построчный: блочный испортил бы весь хвост из-за одной
    # старой cp1251-байты, то есть сломал бы как раз САМЫЕ СВЕЖИЕ строки.
    #
    # BOM: PowerShell 5.1 под именем `utf8` пишет utf-8 С BOM, но только при
    # СОЗДАНИИ файла — проверено 15.08 на 5.1.26100.9168: дописывание в
    # существующий лог BOM не добавляет (позиций EF BB BF в файле нет).
    # На случай нового лога BOM снимается при чтении.
    Add-Content -LiteralPath $gOut -Value $line -Encoding utf8
    Write-Host $line
}

function Write-GuardianBeat {
    [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() |
        Out-File -FilePath $gHbFile -Encoding ascii -Force
}

# ---- single-instance guard (PID lock) --------------------------------------
if (-not $NoLoop) {
    if (Test-Path $lockFile) {
        $old = (Get-Content $lockFile -ErrorAction SilentlyContinue | Select-Object -First 1)
        if ($old) {
            $alive = Get-Process -Id ([int]$old) -ErrorAction SilentlyContinue
            if ($alive -and $alive.ProcessName -match 'powershell|pwsh') {
                Write-G "another chatter guardian already running (PID $old) - exiting"
                return
            }
        }
    }
    $PID | Out-File -FilePath $lockFile -Encoding ascii -Force
    Write-GuardianBeat
    Write-G "chatter guardian started (PID $PID), heartbeat<=${HeartbeatMaxAgeSec}s every ${IntervalSeconds}s, debounce=${DebounceFailures}"
    # Стартовая строка о составе (та, что читала env-переменную персон)
    # приехала из транка и снята при слиянии: гардиан эту переменную больше
    # не ставит — состав приходит планом из реестра, каждому раннеру идёт
    # --client <slug>. Строка печатала бы неизменное «active.yaml / по
    # первому слагу», то есть врала бы ровно там, где должна помогать.
    # Состав виден по per-slug строкам ниже: [slug] launched / runner alive /
    # runner DOWN / invalid.
    #
    # ⚠️ Литерал имени переменной здесь НЕ упоминаем намеренно: тест
    # test_guardian_announces_which_persona_it_deployed ищет его подстрокой в
    # окне после «chatter guardian started», и упоминание в комментарии дало
    # бы ЛОЖНЫЙ ЗЕЛЁНЫЙ (DEV-26) — сторож бы «прошёл» на прозе.
}

function Invoke-WatchCheck {
    # Fire the stdlib-only alerter. It is the SINGLE decision point for both the
    # 🔴 and the paired ✅: it owns the marker and self-dedups, so calling it on
    # any state EDGE is safe and idempotent.
    #
    # PASS OUR VERDICT (-State). We are the authority on DOWN: we check process
    # existence + heartbeat + debounce, the alerter can only stat a file. Without
    # this it re-derived "down" from heartbeat age alone (>180s) and CONTRADICTED
    # us: a killed runner leaves a beat that reads fresh for ~90s more, so the
    # alerter stayed silent through a real outage (drill 13:06:25 -> no 🔴, no ✅).
    #
    # It MUST also be called on the alive edge, not only on DOWN: a recovery can
    # only be observed from the healthy side. Calling it solely in the DOWN branch
    # (the original wiring) made the paired ✅ physically unreachable.
    #
    # -Client: с N клиентами алерт обязан называть, КОГО чинить, и вести в его
    # лог; маркеры тоже per-client, иначе авария одного глушила бы алерт о
    # другом на час кулдауна.
    param([ValidateSet('down', 'up')][string]$State,
          [string]$Client)
    try {
        if ($Client) { & $py $watchCheck --state $State --client $Client 2>$null }
        else         { & $py $watchCheck --state $State 2>$null }
    } catch {
        # DEV-18: an alerter that dies silently is how you lose the next outage.
        Write-G "Invoke-WatchCheck FAILED: $($_.Exception.Message)"
    }
}

function Get-RegistryPlan {
    # Решение (парсинг + валидация реестра) принимает Python — там оно под
    # pytest. Здесь только исполнение. PowerShell не умеет YAML, и держать
    # валидацию конфликта сессий в .ps1 значило бы держать её непокрытой.
    $raw = $null
    try {
        Push-Location $Root
        try { $raw = & $py -m chatter.registry_cli --root $Root 2>$null }
        finally { Pop-Location }
        if (-not $raw) { throw 'registry_cli вернул пустой ответ' }
        return ($raw | ConvertFrom-Json)
    } catch {
        # DEV-18: сломанный реестр обязан быть ВИДИМЫМ, а не уронить гардиан.
        Write-G "Get-RegistryPlan FAILED: $($_.Exception.Message)"
        return [pscustomobject]@{ fatal = $_.Exception.Message; clients = @() }
    }
}

function Get-ChatterProcesses {
    # Все раннеры ЭТОГО инстанса, без различения клиента. -like (не -match):
    # в $Root бэкслеши, в regex они были бы escape-последовательностями.
    #
    # РАЗДЕЛИТЕЛЬ В КОНЦЕ ОБЯЗАТЕЛЕН. Прежний шаблон "*$Root*" матчил любой
    # путь, где $Root просто подстрока: при -Root C:\jarvis под него попадал
    # C:\jarvis_worktrees\... — прод-гардиан считал своими раннеры из
    # worktree-веток и убивал бы их. "*$rootPrefix*" ('C:\jarvis\') такой
    # путь уже не ловит.
    #
    # NOTE: венвовый Scripts\python.exe на Windows ре-экзекает базовый
    # интерпретатор, поэтому здоровый раннер — ДВА процесса (launcher + worker);
    # это норма, нам нужен >=1 живой плюс свежий heartbeat.
    $rootPrefix = $Root.TrimEnd('\') + '\'
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*$rootPrefix*chatter.telethon_run*" }
}

function Get-RunnerProcesses {
    # INSTANCE-SCOPED по $Root И CLIENT-SCOPED по -Slug.
    #
    # Два оператора намеренно:
    #   -like  для $Root/модуля — бэкслеши пути должны остаться литеральными;
    #   -match для --client <slug> — нужна ГРАНИЦА ТОКЕНА. Шаблон
    #          '*--client volska*' через -like поймал бы и '--client volska2',
    #          то есть подъём volska2 убил бы volska.
    #
    # Скоуп по клиенту — причина существования этой функции: раньше матч шёл
    # только по имени модуля, и Stop-OldRunner бил ВСЕХ раннеров сразу, из-за
    # чего запуск второго клиента гасил первого.
    param([Parameter(Mandatory)][string]$Slug)
    $token = '--client\s+' + [regex]::Escape($Slug) + '(\s|$)'
    Get-ChatterProcesses | Where-Object { $_.CommandLine -match $token }
}

function Stop-LegacyRunners {
    # МИНА ДЕПЛОЯ (спека §9.1). Раннер, поднятый ПРЕЖНИМ скриптом, не имеет
    # --client в командной строке, поэтому Get-RunnerProcesses -Slug его не
    # видит. Без этой зачистки новый супервизор решит, что клиент упал, и
    # поднимет ВТОРОЙ процесс на ту же Telethon-сессию — ровно та катастрофа,
    # ради предотвращения которой написана валидация реестра, только
    # протащенная через дверь, которую та не сторожит (она про конфигурацию,
    # а не про то, что уже крутится в памяти).
    #
    # Зовётся ОДИН раз перед первой конвергенцией. После миграции легаси-формы
    # не возникает никогда (супервизор всегда передаёт --client), поэтому
    # зачистка самоустраняется и повторного вреда не несёт.
    $legacy = @(Get-ChatterProcesses | Where-Object { $_.CommandLine -notmatch '--client(\s|$)' })
    foreach ($p in $legacy) {
        & taskkill.exe /PID $p.ProcessId /T /F *> $null
        Write-G "legacy-раннер без --client зачищен (PID $($p.ProcessId)) - миграция §9.1"
    }
    if ($legacy.Count -gt 0) { Start-Sleep -Milliseconds 700 }
}

function Get-HeartbeatAge {
    # Возраст отметки живости клиента в секундах; $null — файла нет/битый.
    param([Parameter(Mandatory)][string]$Slug)
    $hb = (Get-ClientPaths -Slug $Slug).Hb
    if (-not (Test-Path $hb)) { return $null }
    try {
        $last = [int64]((Get-Content $hb -ErrorAction Stop | Select-Object -First 1).Trim())
        return ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - $last)
    } catch { return $null }
}

function Test-Runner {
    # Процесс клиента ДОЛЖЕН существовать И его heartbeat быть свежим.
    # Файл heartbeat переживает смерть процесса и ребут, поэтому одна лишь
    # свежесть соврала бы «жив»; процесс проверяется первым.
    param([Parameter(Mandatory)][string]$Slug)
    if (-not (Get-RunnerProcesses -Slug $Slug)) { return $false }
    $age = Get-HeartbeatAge -Slug $Slug
    if ($null -eq $age) { return $false }   # только что запущен, ещё не готов
    return ($age -le $HeartbeatMaxAgeSec)
}

function Stop-OldRunner {
    # Убить раннер ОДНОГО клиента целым деревом (taskkill /T), затем ДОЖДАТЬСЯ
    # подтверждённой смерти. $false, если кто-то выжил — тогда Start-Runner
    # откажется поднимать второй юзербот поверх живой сессии.
    #
    # -Slug обязателен: без него это была бы прежняя «убить всех», из-за
    # которой подъём второго клиента гасил первого.
    param([Parameter(Mandatory)][string]$Slug, [int]$MaxWaitSec = 10)

    Get-RunnerProcesses -Slug $Slug | ForEach-Object {
        & taskkill.exe /PID $_.ProcessId /T /F *> $null
        Write-G "[$Slug] taskkill sent to runner proc $($_.ProcessId) (cmdline, +tree)"
    }
    $deadline = (Get-Date).AddSeconds($MaxWaitSec)
    while ((Get-RunnerProcesses -Slug $Slug) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 300
    }
    if (Get-RunnerProcesses -Slug $Slug) {
        Write-G "[$Slug] Stop-OldRunner: раннер жив после ${MaxWaitSec}s - НЕ стартую новый (повтор в следующем цикле)"
        return $false
    }
    Start-Sleep -Milliseconds 700
    return $true
}

function Start-Runner {
    # Состав персон, сессия и БД приходят ПЛАНОМ из реестра, а не зашиты здесь:
    # подключение клиента остаётся правкой конфига, а не деплоем скрипта
    # (онбординг-дырка №3). Пути передаём флагами раннера, а не через $env: —
    # переменные окружения процесса гардиана общие для всех клиентов, и второй
    # запуск затирал бы пины первого.
    param(
        [Parameter(Mandatory)][string]$Slug,
        [string[]]$Personas,
        [string]$Session,
        [string]$Db
    )
    if (-not (Stop-OldRunner -Slug $Slug)) {
        Write-G "[$Slug] Start-Runner: старый раннер жив - запуск отменён (никогда не стартуем поверх живой сессии)"
        return $false
    }
    $paths = Get-ClientPaths -Slug $Slug
    $runnerArgs = @('-u', '-m', 'chatter.telethon_run', '--llm', 'real')
    if ($Personas -and $Personas.Count -gt 0) { $runnerArgs += @('--personas', ($Personas -join ',')) }
    if ($Session) { $runnerArgs += @('--session', $Session) }
    if ($Db)      { $runnerArgs += @('--db', $Db) }
    $runnerArgs += @('--client', $Slug)

    $p = $null
    try {
        $p = Start-Process -FilePath $py -ArgumentList $runnerArgs -WorkingDirectory $Root `
            -WindowStyle Hidden -RedirectStandardOutput $paths.Out -RedirectStandardError $paths.Err -PassThru -ErrorAction Stop
    } catch {
        Write-G "[$Slug] Start-Runner: Start-Process FAILED: $($_.Exception.Message)"
        return $false
    }
    # DEV-18: не заявляем успех, которого не получили. Пустой PID = процесс не
    # родился (например, битый редирект) — сказать вслух.
    if (-not $p -or -not $p.Id) {
        Write-G "[$Slug] Start-Runner: запуск не вернул хэндл процесса - считаю провалом"
        return $false
    }
    $p.Id | Out-File -FilePath $paths.Lock -Encoding ascii -Force
    Write-G "[$Slug] launched chatter runner (PID $($p.Id)) -> $($paths.Err)"
    for ($i = 0; $i -lt 45; $i++) {
        Start-Sleep -Seconds 1
        if (Test-Runner -Slug $Slug) { Write-G "[$Slug] runner heartbeat fresh after ~${i}s"; return $true }
    }
    Write-G "[$Slug] runner heartbeat NOT fresh after 45s - повтор в следующем цикле"
    return $false
}

# Состояние супервизора между циклами: дебаунс и рёбра — НА КЛИЕНТА.
# Общие счётчики означали бы, что падение одного клиента сбрасывает дебаунс
# другого и глушит его алерт.
$script:Fail  = @{}
$script:Last  = @{}
$script:Since = @{}

function Set-ClientState {
    param([string]$Slug, [string]$State)
    if ($script:Last[$Slug] -ne $State) { $script:Since[$Slug] = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() }
    $script:Last[$Slug] = $State
}

function Invoke-Converge {
    # Приводит живое к желаемому. -Plan для тестов и повторного использования
    # в одном цикле; без него берём свежий план из реестра.
    param($Plan)
    if (-not $Plan) { $Plan = Get-RegistryPlan }
    if ($Plan.fatal) {
        Write-G "реестр сломан: $($Plan.fatal) - ничего не трогаю до починки"
        return
    }
    foreach ($c in $Plan.clients) {
        $slug = $c.slug
        if (-not $script:Fail.ContainsKey($slug)) { $script:Fail[$slug] = 0 }

        if ($c.runnable) {
            if (Test-Runner -Slug $slug) {
                $script:Fail[$slug] = 0
                if ($script:Last[$slug] -ne 'alive') {
                    Write-G "[$slug] runner alive"
                    Set-ClientState -Slug $slug -State 'alive'
                    Invoke-WatchCheck -State up -Client $slug
                }
                continue
            }
            $script:Fail[$slug]++
            if ($script:Fail[$slug] -lt $DebounceFailures) {
                Write-G "[$slug] проверка не прошла ($($script:Fail[$slug])/$DebounceFailures) - дебаунс, пока не перезапускаю"
                Set-ClientState -Slug $slug -State 'starting'
                continue
            }
            if ($script:Last[$slug] -ne 'down') { Write-G "[$slug] runner DOWN - перезапуск" }
            Set-ClientState -Slug $slug -State 'down'
            Invoke-WatchCheck -State down -Client $slug
            $started = Start-Runner -Slug $slug -Personas $c.personas -Session $c.session -Db $c.db
            if ($started -and (Test-Runner -Slug $slug)) {
                $script:Fail[$slug] = 0
                Set-ClientState -Slug $slug -State 'alive'
                Invoke-WatchCheck -State up -Client $slug
            }
        }
        elseif ($c.error) {
            # СПЕКА §4 ПРАВИЛО 7: не запускаем, но живого НЕ УБИВАЕМ. Иначе
            # опечатка в реестре роняла бы работающего клиента — валидация,
            # написанная ради защиты прода, сама стала бы способом его уронить.
            if ($script:Last[$slug] -ne 'invalid') { Write-G "[$slug] invalid: $($c.error)" }
            Set-ClientState -Slug $slug -State 'invalid'
        }
        else {
            # Выключен намеренно: остановить, если ещё жив.
            if (Get-RunnerProcesses -Slug $slug) {
                Write-G "[$slug] enabled=false - останавливаю"
                Stop-OldRunner -Slug $slug | Out-Null
            }
            Set-ClientState -Slug $slug -State 'stopped'
        }
    }
}

function Write-ClientState {
    # НАБЛЮДАЕМОЕ состояние: кто живёт на самом деле. Дашборд позже читает
    # именно этот файл (а пишет — registry.yaml), поэтому форма фиксирована
    # тестом. Запись атомарная: читатель не должен поймать половину файла.
    param($Plan)
    if (-not $Plan) { $Plan = Get-RegistryPlan }
    $clients = @{}
    foreach ($c in $Plan.clients) {
        $slug = $c.slug
        $procs = @(Get-RunnerProcesses -Slug $slug)
        $age = Get-HeartbeatAge -Slug $slug
        $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()

        if ($c.error)            { $state = 'invalid' }
        elseif (-not $c.runnable) { $state = 'stopped' }
        elseif ($procs.Count -gt 0 -and $null -ne $age -and $age -le $HeartbeatMaxAgeSec) { $state = 'alive' }
        elseif ($script:Last[$slug] -eq 'starting') { $state = 'starting' }
        else                     { $state = 'down' }

        $clients[$slug] = [ordered]@{
            desired            = $c.desired
            state              = $state
            pid                = $(if ($procs.Count -gt 0) { $procs[0].ProcessId } else { $null })
            heartbeat_ts       = $(if ($null -ne $age) { $now - $age } else { $null })
            last_transition_ts = $(if ($script:Since.ContainsKey($slug)) { $script:Since[$slug] } else { $null })
            consecutive_fail   = $(if ($script:Fail.ContainsKey($slug)) { $script:Fail[$slug] } else { 0 })
            last_error         = $c.error
        }
    }
    $payload = [ordered]@{
        updated_ts = [int][DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
        fatal      = $Plan.fatal
        clients    = $clients
    }
    $tmp = "$stateFile.tmp"
    try {
        # UTF-8 БЕЗ BOM. Out-File -Encoding utf8 в PowerShell 5.1 ставит BOM, и
        # json.loads на нём падает ("Unexpected UTF-8 BOM") — файл машинный,
        # его читают Python и будущий дашборд, а не человек в блокноте.
        $json = $payload | ConvertTo-Json -Depth 5
        [System.IO.File]::WriteAllText($tmp, $json, (New-Object System.Text.UTF8Encoding $false))
        Move-Item -LiteralPath $tmp -Destination $stateFile -Force
    } catch {
        Write-G "Write-ClientState FAILED: $($_.Exception.Message)"
    }
}

if (-not $NoLoop) {
    # Одноразовая зачистка легаси-раннеров (спека §9.1) ДО первой конвергенции:
    # процесс, поднятый прежним скриптом, не имеет --client, точечный поиск его
    # не видит, и супервизор поднял бы ВТОРОЙ процесс на ту же сессию.
    Stop-LegacyRunners

    while ($true) {
        Write-GuardianBeat
        $plan = Get-RegistryPlan
        Invoke-Converge -Plan $plan
        Write-ClientState -Plan $plan
        Start-Sleep -Seconds $IntervalSeconds
    }
}
