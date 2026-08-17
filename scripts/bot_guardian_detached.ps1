# Keeps the Jarvis Telegram bot alive, fully detached from any interactive/SSH
# session. Invoked by the JarvisBotGuardian scheduled task (S4U / RunLevel
# Highest, like JarvisBackendGuardian / JarvisSniperDetached) so it survives
# logoff, SSH drops, the starting session, and a reboot (AtStartup trigger) —
# crucially WITHOUT needing an interactive desktop logon.
#
# Design (mirrors backend_guardian_detached.ps1):
#   * Single-instance (P4-style): a PID lockfile stops a second guardian.
#   * Liveness via the bot's own heartbeat file (state\bot_heartbeat.txt, the
#     bot rewrites it every 30s) — catches a hung bot, not just a dead PID.
#   * Bot single-instance: Stop-OldBot clears any stale/hung bot + its pid file
#     before launch, so the bot's own state\bot.pid guard never blocks recovery.
#   * Clean start (P1+P2): the bot self-loads .env + .env.runpod via
#     `from app import env_bootstrap`, so NO secrets are injected here.
#   * PYTHONUTF8=1 prevents the cp1251 emoji crash (memory jarvis-detached-bot-utf8).

param(
    [int]$IntervalSeconds = 30,
    # Раньше 90с; RAM-штормы, которые вызывали транзиентные heartbeat-стойлы,
    # починены батчами регресса (Этап 1, хвост #4) - поднимаем порог, чтобы
    # разовый GC/CPU-стол не читался как смерть бота. Debounce ниже - вторая
    # линия защиты от того же класса ложных срабатываний.
    [int]$HeartbeatMaxAgeSec = 180,
    # Слой C: relaunch только после N подряд неудачных проверок, а не с первой.
    [int]$DebounceFailures = 3,
    # Параметризовано для Python-интеграционных тестов (фейковый долгоживущий
    # процесс + временный $Root) - прод не передаёт этот флаг, дефолт не меняется.
    [string]$Root = 'C:\jarvis',
    # Тестовый хук: только определить функции (Test-Bot/Stop-OldBot/Start-Bot),
    # не брать single-instance lock и не входить в бесконечный цикл. Прод-вызов
    # (register_bot_guardian.ps1) этот флаг не передаёт - поведение не меняется.
    [switch]$NoLoop
)

$ErrorActionPreference = 'Continue'
Set-Location $Root
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$py        = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }
$botFile   = Join-Path $Root 'tools\jarvis_smart_telegram_control.py'
$logDir    = Join-Path $Root 'state\logs'
$lockDir   = Join-Path $Root 'state\locks'
New-Item -ItemType Directory -Force -Path $logDir, $lockDir | Out-Null
$lockFile  = Join-Path $lockDir 'bot_guardian.pid'
$gOut      = Join-Path $logDir 'bot_guardian.stdout.log'
$bOut      = Join-Path $logDir 'bot_boot.stdout.log'
$bErr      = Join-Path $logDir 'bot_boot.stderr.log'
$hbFile    = Join-Path $Root 'state\bot_heartbeat.txt'
$gHbFile   = Join-Path $Root 'state\guardian_heartbeat.txt'
$botPid    = Join-Path $Root 'state\bot.pid'
$regressWatch      = Join-Path $Root 'state\regress_watch.json'
$regressWatchCheck = Join-Path $Root 'scripts\regress_watch_check.py'

function Write-G([string]$msg) {
    $line = ('{0} | {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg)
    # Persist to the durable log file AND echo to the console, but NEVER emit to
    # the PowerShell success (output) stream. The old `Tee-Object -FilePath`
    # passed every line THROUGH to the pipeline, so a function that called Write-G
    # (Stop-OldBot / Start-Bot) returned an ARRAY of log strings + its real value.
    # `if (-not (Stop-OldBot))` then saw a truthy array and NEVER aborted → the
    # guardian launched a SECOND bot on top of a live one, defeating Layer B's core
    # "never start on top of a live poller" guarantee (caught by test_start_bot_
    # aborts_launch_when_old_bot_wont_die). Add-Content + Write-Host keep both the
    # file log and console visibility with a clean, side-effect-free return.
    Add-Content -LiteralPath $gOut -Value $line
    Write-Host $line
}

function Write-GuardianBeat {
    # Own liveness stamp (unix UTC secs), re-written every loop cycle so /health
    # can tell a *live* нянька from one that silently hung — matching how the bot
    # proves itself via state\bot_heartbeat.txt.
    [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() |
        Out-File -FilePath $gHbFile -Encoding ascii -Force
}

# ---- single-instance guard (P4-style PID lock) -----------------------------
# Skipped under -NoLoop: test harness dot-sources this file to reach the
# functions below without taking the real guardian's lock.
if (-not $NoLoop) {
    if (Test-Path $lockFile) {
        $old = (Get-Content $lockFile -ErrorAction SilentlyContinue | Select-Object -First 1)
        if ($old) {
            $alive = Get-Process -Id ([int]$old) -ErrorAction SilentlyContinue
            if ($alive -and $alive.ProcessName -match 'powershell|pwsh') {
                Write-G "another bot guardian already running (PID $old) - exiting"
                return
            }
        }
    }
    $PID | Out-File -FilePath $lockFile -Encoding ascii -Force
    Write-GuardianBeat
    Write-G "bot guardian started (PID $PID), heartbeat<=${HeartbeatMaxAgeSec}s every ${IntervalSeconds}s, debounce=${DebounceFailures}"
}

function Get-BotProcesses {
    # Single source of truth for "what counts as a live bot process", shared by
    # Test-Bot (liveness) and Stop-OldBot (kill target + death poll).
    #
    # INSTANCE-SCOPED to $Root (not a global 'jarvis_smart_telegram_control'
    # substring): the bot is always launched as "$py $botFile" with both paths
    # under $Root, so its cmdline necessarily contains $Root. Scoping by $Root
    # is what makes this hermetically testable AND prod-safe — a Python-integration
    # test running with a temp $Root can never match (let alone taskkill) a REAL
    # bot living under C:\jarvis, and a second bot under a different root can't be
    # mistaken for ours. -like (not -match) so the backslashes in the path are
    # treated literally, not as regex.
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*$Root*jarvis_smart_telegram_control*" }
}

function Test-Bot {
    # A bot process MUST exist. The heartbeat file persists after the bot dies
    # (or across a reboot), so freshness alone would falsely report "alive" —
    # require the process first, then use the heartbeat to also catch a hung one.
    $proc = Get-BotProcesses
    if (-not $proc) { return $false }
    if (-not (Test-Path $hbFile)) { return $false }  # just launched, not ready yet
    try {
        $last = [int64]((Get-Content $hbFile -ErrorAction Stop | Select-Object -First 1).Trim())
        $now  = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
        return (($now - $last) -le $HeartbeatMaxAgeSec)
    } catch { return $false }
}

# ── Диагноз в момент решения «бот DOWN» ──────────────────────────────────────
#
# 17.08: бот перезапускался ШЕСТЬ раз за сутки (03:59, 13:05, 14:55, 15:08,
# 15:39, 16:17), и каждый раз — во время полного прогона гейтов. Ротация
# boot-логов показала, что Python-исключения не было: архив умершего
# экземпляра обрывается на обычных строках старта, без трассировки.
#
# Значит остаётся ровно два объяснения, и различить их можно только ЗДЕСЬ,
# ДО того, как Stop-OldBot снесёт процесс:
#   * процесс МЁРТВ  -> он умер сам (OOM-киллер, аварийный выход);
#   * процесс ЖИВ    -> heartbeat не писался при живом процессе, то есть
#                       голодание или заклинивший event loop, и чинить надо
#                       порог/приоритет, а не бота.
#
# Совпадение времени с гейтами сильное, но принимать его на веру нельзя:
# у нас уже был случай, когда очевидная причина оказалась третьей
# (`-Db` против алиаса `-Debug`). Поэтому строка несёт ЗАМЕР нагрузки, а не
# вывод: живость, возраст heartbeat, загрузку CPU, свободную память и число
# идущих pytest-прогонов.
function Get-ExitCodeVerdict {
    param($Code)
    # Расшифровка ЗДЕСЬ, а не в голове разбирающего: через неделю никто не
    # вспомнит, что 1 — это taskkill, а 0 — это два os._exit(0) в боте.
    if ($null -eq $Code) { return "код выхода недоступен (хэндла нет)" }
    $c = [int64]$Code
    $hex = "0x{0:X8}" -f ($c -band 0xFFFFFFFF)
    switch ($c) {
        0 { return "код 0 - вышел САМ (os._exit(0): dev_task merge или /restart_bot)" }
        1 { return "код 1 - УБИТ снаружи (taskkill /F = TerminateProcess)" }
        default {
            if (($c -band 0xC0000000) -eq 0xC0000000 -or $hex -like '0xC0*') {
                return "код $hex - КРАХ процесса (NTSTATUS)"
            }
            return "код $c ($hex) - ни 0, ни 1: смотреть вручную"
        }
    }
}

function Get-DownDiagnosis {
    param(
        [bool]$Alive, [int]$HeartbeatAgeSec, [int]$CpuPercent,
        [int]$FreeRamMb, [int]$PytestCount, [string]$BotPid = '-',
        [string]$ExitVerdict = ''
    )
    $state = if ($Alive) { "процесс ЖИВ (PID $BotPid) - heartbeat не писался при живом процессе" }
             else        { "процесс МЁРТВ - умер сам" }
    $tail = if ($ExitVerdict) { " | $ExitVerdict" } else { "" }
    return ("DIAGNOSIS: {0} | heartbeat {1}s назад | CPU {2}% | RAM свободно {3} МБ | pytest-прогонов {4}{5}" `
            -f $state, $HeartbeatAgeSec, $CpuPercent, $FreeRamMb, $PytestCount, $tail)
}

function Measure-DownContext {
    # Снимок машины на момент решения. Всё через try: замер НЕ ИМЕЕТ ПРАВА
    # мешать подъёму бота, а отсутствующее число обязано быть видно как -1,
    # а не как ноль (ноль читался бы как «нагрузки нет»).
    $proc = @(Get-BotProcesses)
    $alive = [bool]$proc
    $botPid = if ($alive) { [string]$proc[0].ProcessId } else { '-' }

    $hbAge = -1
    try {
        if (Test-Path $hbFile) {
            $last = [int64]((Get-Content $hbFile -ErrorAction Stop | Select-Object -First 1).Trim())
            $hbAge = [int]([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - $last)
        }
    } catch { $hbAge = -1 }

    $cpu = -1
    try { $cpu = [int]((Get-CimInstance Win32_Processor -ErrorAction Stop |
                        Measure-Object -Property LoadPercentage -Average).Average) } catch { $cpu = -1 }

    $freeMb = -1
    try { $freeMb = [int](((Get-CimInstance Win32_OperatingSystem -ErrorAction Stop).FreePhysicalMemory) / 1024) } catch { $freeMb = -1 }

    $pytest = -1
    try {
        $pytest = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction Stop |
                    Where-Object { $_.CommandLine -like '*pytest*' }).Count
    } catch { $pytest = -1 }

    # Код выхода спрашиваем ТОЛЬКО у мёртвого и только по своему хэндлу: у
    # живого его нет по определению, а чужой процесс о своём коде не расскажет.
    $exitVerdict = ''
    try {
        if (-not $alive -and $script:BotProc -and $script:BotProc.HasExited) {
            $exitVerdict = Get-ExitCodeVerdict $script:BotProc.ExitCode
        }
    } catch { $exitVerdict = "код выхода не прочитался ($($_.Exception.GetType().Name))" }

    return (Get-DownDiagnosis -Alive $alive -HeartbeatAgeSec $hbAge -CpuPercent $cpu `
                              -FreeRamMb $freeMb -PytestCount $pytest -BotPid $botPid `
                              -ExitVerdict $exitVerdict)
}

function Stop-OldBot {
    # Layer B: kill by PID-from-file AND by cmdline match, both as a full
    # process TREE (taskkill /T), then POLL for confirmed death up to
    # $MaxWaitSec before deciding. bot.pid is only removed once nothing
    # matching the bot is left alive - if a process survives past the
    # deadline, this returns $false and leaves bot.pid in place so Start-Bot
    # (below) refuses to launch a second poller on top of a live one.
    param([int]$MaxWaitSec = 10)

    $opPid = $null
    if (Test-Path $botPid) {
        try {
            $opPid = [int]((Get-Content $botPid -ErrorAction Stop) -join '').Trim()
            if (Get-Process -Id $opPid -ErrorAction SilentlyContinue) {
                & taskkill.exe /PID $opPid /T /F *> $null
                Write-G "taskkill sent to stale bot PID $opPid (pid file, +tree)"
            }
        } catch { $opPid = $null }
    }
    Get-BotProcesses | ForEach-Object {
        & taskkill.exe /PID $_.ProcessId /T /F *> $null
        Write-G "taskkill sent to bot proc $($_.ProcessId) (cmdline, +tree)"
    }

    function Test-BotStillAlive {
        if ($opPid -and (Get-Process -Id $opPid -ErrorAction SilentlyContinue)) { return $true }
        if (Get-BotProcesses) { return $true }
        return $false
    }

    $deadline = (Get-Date).AddSeconds($MaxWaitSec)
    while ((Test-BotStillAlive) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 300
    }

    if (Test-BotStillAlive) {
        Write-G "Stop-OldBot: bot still alive after ${MaxWaitSec}s - NOT clearing pid, NOT starting new (retry next cycle)"
        return $false
    }

    if (Test-Path $botPid) { Remove-Item $botPid -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 700
    return $true
}

# Последние слова умирающего экземпляра переживают подъём следующего.
#
# `Start-Process -RedirectStandardError` ОБРЕЗАЕТ файл при каждом запуске, и
# 17.08 это стоило разбора: бот перезапускался четырежды за сутки (01:08,
# 03:14, 03:59, 13:05), каждый раз на третьей провалившейся проверке
# heartbeat, и каждый раз — во время полного гейта. Отличить голодание под
# нагрузкой от настоящей смерти процесса было НЕЧЕМ: трассировку падения
# затирал тот самый подъём, который её и расследует.
#
# Храним 12 последних (около суток при нынешней частоте). Имя — по времени
# ПОСЛЕДНЕЙ ЗАПИСИ файла, а не по времени ротации: интересует момент смерти.
function Rotate-BootLog {
    param([string]$Path, [int]$Keep = 12)
    if (-not (Test-Path $Path)) { return }
    $item = Get-Item $Path -ErrorAction SilentlyContinue
    if (-not $item -or $item.Length -eq 0) { return }   # пустой хранить незачем
    $stamp = $item.LastWriteTime.ToString('yyyyMMdd-HHmmss')
    $dir   = Split-Path $Path -Parent
    $base  = [IO.Path]::GetFileNameWithoutExtension($Path)
    $ext   = [IO.Path]::GetExtension($Path)
    # Секунды на имя мало: два падения подряд (или перезапуск сразу после
    # неудачного подъёма) укладываются в одну, и вторая улика затёрла бы
    # первую МОЛЧА. Мутационный гейт поймал это на живом коде: без счётчика
    # пять ротаций подряд дают два файла вместо пяти.
    $target = Join-Path $dir "$base.$stamp$ext"
    $n = 1
    while (Test-Path $target) {
        $target = Join-Path $dir "$base.$stamp-$n$ext"
        $n++
    }
    try { Move-Item $Path $target -Force -ErrorAction Stop }
    catch {
        # Ротация НЕ ИМЕЕТ ПРАВА мешать подъёму бота: лог — это удобство
        # разбора, а бот — прод. Не смогли сохранить — говорим и идём дальше.
        Write-G "Rotate-BootLog: не смог сохранить $Path ($($_.Exception.GetType().Name)) - продолжаю подъём"
        return
    }
    Get-ChildItem -Path $dir -Filter "$base.*$ext" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -Skip $Keep |
        ForEach-Object { Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue }
}

function Start-Bot {
    if (-not (Stop-OldBot)) {
        Write-G "Start-Bot: old bot still alive - aborting launch (never start on top of a live poller)"
        return $false
    }
    Rotate-BootLog $bOut
    Rotate-BootLog $bErr
    $p = Start-Process -FilePath $py -ArgumentList @($botFile) -WorkingDirectory $Root `
        -WindowStyle Hidden -RedirectStandardOutput $bOut -RedirectStandardError $bErr -PassThru
    # Держим объект процесса, чтобы после смерти спросить у ОС КОД ВЫХОДА.
    # Отпечаток (`bot_death.log`) 17.08 дал вердикт «killed»: ни трассировки, ни
    # метки чистого выхода. Но он по построению НЕ различает внешнее убийство и
    # `os._exit()` — оба обрывают процесс без atexit. Код выхода различает:
    #   1          — `taskkill /F` (TerminateProcess с кодом 1), кто-то убил;
    #   0          — процесс вышел сам (в боте это ровно два os._exit(0));
    #   0xC0000409 — fail-fast CRT, 0xC0000005 — access violation, то есть крах.
    # Хэндл живёт только у ЗАПУСТИВШЕГО, поэтому спросить может лишь гардиан.
    $script:BotProc = $p
    Write-G "launched bot (PID $($p.Id)) -> $bOut"
    for ($i = 0; $i -lt 45; $i++) {
        Start-Sleep -Seconds 1
        if (Test-Bot) { Write-G "bot heartbeat fresh after ~${i}s"; return $true }
    }
    Write-G "bot heartbeat NOT fresh after 45s - will retry next cycle"
    return $false
}

if (-not $NoLoop) {
    $lastState = ''
    $consecutiveFail = 0   # Layer C: consecutive failed Test-Bot checks
    while ($true) {
        Write-GuardianBeat   # prove the нянька is awake every cycle, before any work

        # Detached regress watchdog, layer 2 (Этап 1, хвост #6): if a regress marker
        # exists, run the standalone stdlib-only killer. It no-ops for a healthy run;
        # if the regress is past its deadline OR orphaned (its parent bot PID dead),
        # it taskkills the PID-group, TG-alerts the admin, and clears the marker —
        # the case the bot's own loop can't cover because the bot itself has died.
        # Cheap: only spawns Python when the marker is actually present.
        if (Test-Path $regressWatch) {
            try { & $py $regressWatchCheck 2>$null } catch {}
        }

        if (Test-Bot) {
            $consecutiveFail = 0
            if ($lastState -ne 'alive') { Write-G 'bot alive'; $lastState = 'alive' }
        } else {
            $consecutiveFail++
            if ($consecutiveFail -lt $DebounceFailures) {
                Write-G "bot check failed (${consecutiveFail}/${DebounceFailures}) - debouncing, not relaunching yet"
            } else {
                if ($lastState -ne 'dead') { Write-G 'bot DOWN - restarting'; $lastState = 'dead' }
                # ЗАМЕР ДО СНОСА: Start-Bot зовёт Stop-OldBot, и после него
                # вопрос «был ли процесс жив» становится неотвечаемым навсегда.
                try { Write-G (Measure-DownContext) }
                catch { Write-G "DIAGNOSIS: замер не удался ($($_.Exception.GetType().Name)) - продолжаю подъём" }
                $started = Start-Bot
                if ($started -and (Test-Bot)) {
                    $lastState = 'alive'
                    $consecutiveFail = 0
                } else {
                    # Dev-task (Ступень 2) crash-loop guard: if a [Мердж] wrote a
                    # boot_watch marker and the merged code never boots healthy past its
                    # deadline, this standalone stdlib-only script TG-alerts the admin
                    # with rollback commands. Survives even a merge that breaks the bot's
                    # imports. No-op when no marker / bot already healthy.
                    try { & $py (Join-Path $Root 'scripts\boot_watch_check.py') 2>$null } catch {}
                }
            }
        }
        Start-Sleep -Seconds $IntervalSeconds
    }
}
