"""Сборщик состояния фермы для панели Джарвиса (docs/jarvis-panel/PHASE0_READONLY.md).

СТРОГО read-only: ничего не запускает, не убивает и не правит. Единственная
запись — собственный снапшот (кэш в памяти процесса).

Три грабли зашиты здесь намеренно, потому что все три уже стоили ложных выводов:
  1. фильтр процессов по CommandLine МАТЧИТ САМ СЕБЯ (строка попадает в
     командную строку сборщика) → «раннер жив», когда его нет. Разбор — в
     `_launch_argv`: маркер засчитывается только в аргументах ЗАПУСКА;
  2. LastTaskResult у Running-таска ничего не значит;
  3. на NTFS размер живого лога врёт (0 байт при открытом write-хэндле) —
     возраст берём временем модификации/чтением, не размером.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(os.getenv("JARVIS_ROOT", "C:/jarvis"))
HEARTBEAT_FRESH = 90.0

# Маркер запуска раннера chatter. Именованный, потому что адресуется из ДВУХ
# мест: списка процессов и `client_db_path`, которая спрашивает у живого раннера
# его базу. Разъехавшись, литералы дали бы панель, которая раннера видит, но
# спросить не может.
CHATTER_RUNNER = "chatter.telethon_run"

# Процессы фермы: (ключ, подпись, маркер в командной строке, только python?)
PROC_SPECS = [
    ("backend",  "backend :8010",  "run_backend_detached", True),
    ("bot",      "главный бот",    "jarvis_smart_telegram_control", True),
    ("chatter",  "chatter раннер", CHATTER_RUNNER, True),
]

GUARDIAN_SPECS = [
    ("backend_guardian", "backend_guardian_detached.ps1", "guardian_heartbeat.txt"),
    ("bot_guardian",     "bot_guardian_detached.ps1",     "bot_heartbeat.txt"),
    ("chatter_guardian", "chatter_guardian_detached.ps1", "chatter_guardian_heartbeat.txt"),
    ("ops_watchdog",     "ops_watchdog_detached.ps1",     "ops_watchdog_heartbeat.txt"),
]

# Отставлено НАМЕРЕННО — отдельный класс, не авария. Панель, красящая это
# красным, ежедневно требует чинить нечинимое, и её перестают читать.
RETIRED = {"JarvisSniperDetached": "RunPod-пивот: триггеров нет, сам не поднимется"}

# ─────────────────── Кто кого поднимает (заход 1, §1.1) ─────────────────────
#
# Связи процесс↔гардиан в коде не было вовсе, и панель не могла отличить два
# РАЗНЫХ события: «упало и поднимется само через полторы минуты» (14.08 00:45,
# раннер вернулся за 61 с) и «упало, поднимать некому». Оба давали «Впало: 1».
PAIRS = {"backend": "backend_guardian", "bot": "bot_guardian",
         "chatter": "chatter_guardian"}   # ops_watchdog пары не имеет: он сторожит сторожей

# Через сколько секунд живой гардиан поднимет упавшее. Числа — ИЗ КОНСТАНТ
# самих гардианов, а не на глаз:
#   scripts/chatter_guardian_detached.ps1 : IntervalSeconds=30 × DebounceFailures=3
#   scripts/bot_guardian_detached.ps1     : IntervalSeconds=30 × DebounceFailures=3
#   scripts/backend_guardian_detached.ps1 : IntervalSeconds=15
# ⚠️ Числа продублированы в PowerShell и здесь. Разъедутся — соврёт вторая
# строка ответа, а код при этом не упадёт; поэтому путь к источнику записан
# рядом, и правку делать в ОБОИХ местах.
GUARDIAN_ETA = {"backend_guardian": 15, "bot_guardian": 90, "chatter_guardian": 90}

# ─────────────────── Пороги «аномалия или состояние» ────────────────────────
#
# Тест принадлежности один: изменит ли это моё поведение в ближайший час?
# Пороги живут ЗДЕСЬ именованными константами, а не литералами по коду и не в
# глазах смотрящего.
KEY_EXPIRY_ANSWER = 7     # ключ истекает раньше — уходит в ОТВЕТ (L5)
KEY_EXPIRY_ANOMALY = 30   # раньше — в блок аномалий, но не в ответ
SLOW_TTL = 300.0          # медленный кэш старше — считается непрочитанным


@dataclass
class Row:
    key: str
    label: str
    state: str            # ok | warn | bad | off
    detail: str = ""
    extra: dict = field(default_factory=dict)


def _now() -> float:
    return time.time()


# ─────────── ГРАБЛЯ 1: запуск против упоминания (правка 14.08) ──────────────
#
# Раньше самосовпадение лечилось исключением по РОДСТВУ: выбрасывали свой PID и
# всех предков. Пока сборщик был отдельным процессом, это работало. Панель
# переехала внутрь бэкенда — и то же правило выбросило разом сам бэкенд
# (`os.getpid()`), его второй PID (родитель) и `backend_guardian_detached.ps1`
# (дед). Панель отдавала страницу ИЗ процесса и на ней же писала «процес не
# знайдено» о нём. Родство — плохой признак: оно отвечает на вопрос «мой ли это
# процесс», а спрашивали мы «запускает он раннер или только упоминает его».
#
# Спрашиваем теперь ровно это. Всё, что идёт после `-c` (python) и
# `-Command`/`-EncodedCommand` (PowerShell), — ТЕЛО ПРОГРАММЫ: совпадение там
# значит, что процесс говорит о раннере, а не является им. Проверка строго
# сильнее прежней: диагностический однострочник теперь не совпадает НИ У КОГО, а
# не только у собственных потомков, — а `healthchecks_ping.ps1` с именами
# скриптов внутри `-Command` до этой правки засчитывался как живой гардиан
# (у гардианов фильтра `py_only` нет вовсе).
def _inline_flag(name: str, tok: str) -> bool:
    if not tok.startswith("-"):
        return False
    flag = tok.lstrip("-/").lower()
    if name.startswith(("powershell", "pwsh")):
        # PowerShell принимает сокращения: -Comm, -enc, -ec. `-ExecutionPolicy`
        # под это не подпадает («ex» ≠ «en»), и значение флага остаётся видимым.
        return bool(flag) and ("command".startswith(flag)
                               or "encodedcommand".startswith(flag))
    # У python inline ровно один: `-c`. Префиксного разбора здесь быть не может
    # — `-E` это «игнорировать переменные окружения», и принять его за inline
    # значит сделать процесс невидимым.
    return flag == "c"


def _launch_argv(name: str, argv: list[str]) -> list[str]:
    """Аргументы, в которых маркер означает ЗАПУСК, а не упоминание.

    Граница «флаг / тело inline-кода» существует только в ТОКЕНАХ: в склеенной
    командной строке её уже нет, поэтому таблица процессов носит argv списком.
    """
    out = []
    for tok in argv[1:]:
        if _inline_flag(name, tok):
            break
        out.append(tok)
    return out


def _launches(marker: str, row: tuple) -> bool:
    return any(marker in tok for tok in _launch_argv(row[1], row[2]))


# Имена процессов, чью КОМАНДНУЮ СТРОКУ вообще имеет смысл читать.
#
# Замер 14.08: `process_iter(["pid","name"])` по 217 процессам — 0.00 с, тот же
# обход с `cmdline` — 1.05 с (psutil открывает PEB каждого процесса), и панель
# делала его ДВАЖДЫ: отдельно в `processes()` и отдельно в `guardians()`. Отсюда
# 2.2 с «быстрой» части, которая по замыслу должна стоить миллисекунды.
#
# Сузить безопасно: все маркеры фермы — это python-раннеры (PROC_SPECS, все с
# py_only) и powershell-гардианы (GUARDIAN_SPECS, все `*.ps1`). Процесс с любым
# другим именем не мог бы совпасть с маркером и без этого фильтра. Появится
# служба под собственным .exe — её имя надо будет добавить СЮДА, иначе она
# станет невидимой молча.
_CMDLINE_NAMES = ("python", "pythonw", "powershell", "pwsh")


def _proc_table() -> list[tuple]:
    """(pid, имя, argv СПИСКОМ, время старта, ppid) — один обход на оба
    сборщика. Пустой список означает «psutil не отдал ничего», и это НЕ то же
    самое, что «psutil недоступен»: второе ловится отдельно, в `processes()`.

    argv именно списком, а не склеенной строкой: `_launch_argv` отличает запуск
    от упоминания по границам токенов, и склейка эту границу стирает."""
    try:
        import psutil
    except Exception:
        return []
    out = []
    for p in psutil.process_iter(["pid", "name", "create_time", "ppid"]):
        try:
            nm = (p.info["name"] or "").lower()
            if not nm.startswith(_CMDLINE_NAMES):
                continue
            out.append((p.info["pid"], nm, p.cmdline() or [],
                        p.info["create_time"], p.info["ppid"]))
        except Exception:
            continue
    return out


def processes(table: list[tuple] | None = None) -> list[Row]:
    try:
        import psutil  # noqa: F401 — проверка доступности, обход в _proc_table
    except Exception:
        return [Row("procs", "процессы", "warn", "psutil недоступен")]

    snap = _proc_table() if table is None else table

    rows = []
    for key, label, marker, py_only in PROC_SPECS:
        hits = [
            s for s in snap
            if (not py_only or s[1].startswith("python"))
            and _launches(marker, s)                 # ГРАБЛЯ 1: запуск, не упоминание
        ]
        if hits:
            pids = "/".join(str(h[0]) for h in hits[:3])
            started = min(h[3] for h in hits)
            rows.append(Row(key, label, "ok", f"PID {pids}",
                            {"since": started, "count": len(hits)}))
        else:
            rows.append(Row(key, label, "bad", "процесс не найден"))
    return rows


def guardians(table: list[tuple] | None = None) -> list[Row]:
    rows = []
    # Тот же обход процессов, что у `processes()`: раньше здесь был ВТОРОЙ
    # полный проход с чтением cmdline — ровно та секунда, которую первый экран
    # платил дважды ни за что.
    snap = _proc_table() if table is None else table
    for key, script, hb in GUARDIAN_SPECS:
        alive = [s for s in snap if _launches(script, s)]
        age = heartbeat_age(hb)
        fresh = age is not None and age < HEARTBEAT_FRESH
        if alive and fresh:
            state, detail = "ok", f"PID {alive[0][0]} · heartbeat {int(age)} с назад"
        elif alive or fresh:
            # ЖЁЛТЫЙ — не косметика: расхождение «процесс жив, heartbeat мёртв»
            # это ровно тот случай, когда сторож и гардиан считали DOWN по-разному.
            state = "warn"
            detail = ("процесс есть, heartbeat протух" if alive
                      else "heartbeat свежий, процесса не видно")
        else:
            state, detail = "bad", "не работает"
        rows.append(Row(key, key, state, detail, {"hb_age": age}))
    return rows


def heartbeat_age(name: str) -> float | None:
    """Возраст heartbeat-файла. ГРАБЛЯ 3: размер файла на NTFS врёт, поэтому
    смотрим mtime, а не длину."""
    p = ROOT / "state" / name
    try:
        return _now() - p.stat().st_mtime
    except OSError:
        return None


def scheduled_tasks() -> list[Row]:
    """Плановые задачи. ГРАБЛЯ 2: у Running-таска LastTaskResult не значит
    ничего (у живых гардианов там 2147946720) — авторитетен State."""
    ps = (
        "Get-ScheduledTask | Where-Object { $_.TaskName -match 'Jarvis' } | "
        "ForEach-Object { $i = Get-ScheduledTaskInfo -TaskName $_.TaskName; "
        "\"$($_.TaskName)|$($_.State)|$($i.LastTaskResult)|$($i.LastRunTime)\" }"
    )
    try:
        out = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive",
                              "-Command", ps], capture_output=True, timeout=30,
                             text=True, encoding="utf-8", errors="replace").stdout
    except Exception as e:
        return [Row("tasks", "плановые задачи", "warn", f"не прочитано: {type(e).__name__}")]

    rows = []
    for line in (out or "").splitlines():
        parts = line.strip().split("|")
        if len(parts) < 4:
            continue
        name, state, last, when = parts[0], parts[1], parts[2], parts[3]
        if name in RETIRED:
            rows.append(Row(name, name, "off", RETIRED[name], {"last_run": when}))
            continue
        if state == "Running":
            rows.append(Row(name, name, "ok", "Running", {"last_run": when}))
        elif state == "Ready":
            ok = last.strip() in ("0", "267009")
            rows.append(Row(name, name, "ok" if ok else "warn",
                            f"Ready · последний результат {last}", {"last_run": when}))
        else:
            rows.append(Row(name, name, "warn", state, {"last_run": when}))
    return rows


def _git(args: list[str], cwd: Path | None = None) -> str:
    try:
        return subprocess.run(["git"] + args, cwd=str(cwd or ROOT), capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=30).stdout.strip()
    except Exception:
        return ""


def arcs() -> list[dict]:
    """Ветки/worktree с возрастом. Возраст — та колонка, которой сейчас нет
    нигде, и именно она превращает список в решение («мёртвое — снести»)."""
    out = []
    raw = _git(["worktree", "list", "--porcelain"])
    cur: dict = {}
    for line in raw.splitlines():
        if line.startswith("worktree "):
            if cur:
                out.append(cur)
            cur = {"path": line.split(" ", 1)[1]}
        elif line.startswith("branch "):
            # Только refs/heads/, а не последний сегмент: split("/")[-1] резал
            # «arc/p20-suppression» до «p20-suppression» — и имя врало, и сверка
            # со списком смерженных промахивалась.
            cur["branch"] = line.split(" ", 1)[1].replace("refs/heads/", "", 1)
        elif line.startswith("detached"):
            cur["branch"] = "(detached)"
    if cur:
        out.append(cur)

    # Список смерженных считаем ОДИН раз, а не в цикле по 21 worktree.
    merged_names = {b.strip().lstrip("* ").strip()
                    for b in _git(["branch", "--merged", "phase-4.0-unified-jarvis"]).splitlines()}
    for w in out:
        p = Path(w["path"])
        w["dirty"] = bool(_git(["status", "--porcelain"], p))
        last = _git(["log", "-1", "--format=%ct|%s"], p)
        if "|" in last:
            ts, subj = last.split("|", 1)
            try:
                w["age_days"] = round((_now() - float(ts)) / 86400.0, 1)
            except ValueError:
                w["age_days"] = None
            w["subject"] = subj[:70]
        w["merged"] = w.get("branch", "") in merged_names
    return out


# ─────────────────────── Лента: время строк гардиана ────────────────────────
_LOG_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \| ")


def parse_log_ts(line: str) -> float | None:
    """Время из префикса строки гардиана.

    ⚠️ Время в логе ЛОКАЛЬНОЕ и без зоны, поэтому `time.mktime` (он трактует
    struct_time как локальное), а НЕ `calendar.timegm`. Принять его за UTC
    значит сдвинуть всю ленту на три часа и получить события из будущего.

    Строка без разбираемого префикса получает None и рисуется прочерком:
    выдуманное время хуже отсутствующего — оно выглядит достоверным.

    ⚠️ Пробел (принятый, не недосмотр): `time.strptime` всегда ставит
    `tm_isdst = -1`, и на часе весеннего DST-перевода (последнее воскресенье
    марта, тот самый несуществующий локальный час 03:00–03:59)
    `time.mktime` тихо угадывает флаг — и угадывает неверно, без исключения.
    Строка из этого часа встанет в ленте на час раньше настоящего времени.
    Чинить `zoneinfo` ради одного часа в году для «что было недавно»
    несоразмерно; если понадобится точность — переходить на
    `datetime.fromisoformat(...).replace(tzinfo=ZoneInfo(...), fold=1)`.
    """
    if not isinstance(line, str):
        return None
    m = _LOG_TS_RE.match(line)
    if not m:
        return None
    try:
        return time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
    except (ValueError, OverflowError):
        return None


# Что из лога гардиана считается СОБЫТИЕМ, а что — его собственным шумом.
#
# Замер 14.08 по живому логу (421 строка): 163 строки, то есть 39%, — это
# «runner check failed (N/3) - debouncing», по две на каждый несостоявшийся
# инцидент. Прежний фильтр искал подстроки `DOWN`/`launched`/`failed`, ловил
# дебаунс целиком: в последних 40 строках лога под него попадали 28
# (`tail -n 40 logs/chatter_guardian.stdout.log | grep -Ec "DOWN|launched|failed"`),
# и ровно половина из этих 28 — сам дебаунс-шум (14 `debouncing`, остальные
# 14 — настоящие решения: `runner DOWN` ×7, `launched` ×7). Шум и решение
# делили экран поровну — не «шум почти всё съедал», а «шум отжирал
# половину полезного окна».
#
# Список ниже выведен ПЕРЕЧИСЛЕНИЕМ вызовов `Write-G` в
# scripts/chatter_guardian_detached.ps1, а не по тому, что попало в окно
# лога за 14.08 — окно однодневного лога может не содержать редкий вид
# строки (например «another chatter guardian already running» происходит
# только при гонке двух гардианов), а сторож ленты обязан ловить его сразу,
# не когда он наконец случится в проде.
#
# Список ИМЕНОВАННЫЙ намеренно: новый вид строки не попадёт в ленту молча —
# его придётся добавить сюда осознанно. Суп из подстрок делал обратное.
GUARDIAN_DECISIONS = (
    "runner DOWN",                 # решение поднимать
    "launched chatter runner",     # подъём состоялся
    "chatter guardian started",    # рестарт самого сторожа
    "heartbeat NOT fresh",         # настоящий провал подъёма
    "CHATTER_PERSONAS=",           # строка «Состав: … db=…», называет активную базу
    "already running",             # гонка двух гардианов — новый не поднялся
    "still alive",                 # старый раннер не умер — новый не стартовал
    "FAILED",                      # ЗАГЛАВНЫМИ: Start-Process/Invoke-WatchCheck не
                                    # смогли выполниться. Регистр несущий: не пересекается
                                    # с дебаунсной `runner check failed` (строчными)
    "ДЕМО ЯРИНЫ",                  # подмена состава демо-флагом — гасит прод-персону
)
# Строки, которые под маску решения попадают, но событием не являются.
# Проверяется ПЕРВЫМ — не потому, что сегодня в живом логе есть строка,
# несущая оба признака разом (такой нет: ни одна DROP/KEEP строка не
# содержит и шум, и слово решения), а как правило на будущее: шум обязан
# перебивать решение, если такая строка появится. На это правило заведён
# отдельный сторож с синтетической строкой (test_noise_outranks_a_decision_word_in_the_same_line).
GUARDIAN_NOISE = ("debouncing", "runner alive", "heartbeat fresh after")
# Сознательно НЕ в списке ни разу: `Write-G "taskkill sent to runner proc ..."`
# (Stop-OldRunner) — механическая деталь ХОДА перезапуска, а не решение; она
# уже обрамлена решениями `runner DOWN` и `launched chatter runner`, которые
# и попадают в ленту. Добавлять её значило бы вернуть суп из подстрок с
# другой стороны — шум, который выглядит как решение.


def is_decision(line: str) -> bool:
    """Решение гардиана против его же дебаунс-шума.

    Контракт на мусор — тот же, что у соседней `parse_log_ts`: обе разбирают
    одни и те же строки одного источника, и расходиться в поведении на
    негодном входе им нельзя. Сегодня `read_tail` отдаёт только `str`, так
    что ветка недостижима, — она держит РАВЕНСТВО контрактов, а не случай.
    """
    if not isinstance(line, str):
        return False
    if any(noise in line for noise in GUARDIAN_NOISE):
        return False
    return any(mark in line for mark in GUARDIAN_DECISIONS)


# Сколько байт хвоста лога читаем. Ротации у chatter_guardian_detached.ps1 нет
# ВООБЩЕ (проверено 14.08: ни Clear-Content, ни лимита, ни слова rotate — ни в
# гардиане, ни в остальных scripts/*.ps1), лог растёт вечно, и `read_text()`
# дорожал бы с каждым днём.
#
# 64 КБ — это ≈870 строк лога: замер 14.08 дал 31 677 байт на 421 строку,
# то есть 75 байт на строку. Заведомо больше окна ленты и заведомо дёшево.
# Там же измерен рост: 1114 байт в сутки, то есть ветка усечения впервые
# исполнится в проде около 13.09.2026 — до тех пор она проверяется ТОЛЬКО
# сторожами, и сломать её незаметно легко.
#
# ⚠️ `stat()` по ИМЕНИ здесь безопасен, хотя грабля №3 в заголовке модуля
# запрещает верить размеру живого лога. Врёт ленивая запись каталога, которую
# читают `dir`/`Get-ChildItem`/`Get-Item`; `os.stat(path)` открывает файл и
# возвращает правду (замер 14.08: delta=0 на четырёх живых логах фермы и на
# 1.32 МБ, дописанных через незакрытый write-хэндл).
GUARDIAN_LOG_WINDOW = 64 * 1024


def _decode_line(chunk: bytes) -> str:
    """Одна строка лога: utf-8, и только на ней самой — фолбэк в cp1251.
    Обоснование порядка и области фолбэка — в докстринге `read_tail`."""
    try:
        return chunk.decode("utf-8")
    except UnicodeDecodeError:
        return chunk.decode("cp1251", errors="replace")


def read_tail(path: "Path | str",
              limit: int = GUARDIAN_LOG_WINDOW) -> tuple[list[str], bool]:
    """(строки хвоста, файл_длиннее_окна).

    КОДИРОВКА: utf-8, при провале — cp1251, и решается это ПОСТРОЧНО. Порядок
    не переставляется: cp1251 отображает 255 байт из 256 и на живом логе не
    споткнётся никогда, поэтому первым он молча превратил бы нормальный utf-8
    в мусор, и выглядело бы это как «так и было в логе».

    `errors="replace"` при этом НЕ лишний: единственный неопределённый в
    cp1251 байт — `0x98`, и без глушителя одна такая байта в логе роняет
    `UnicodeDecodeError` наружу, то есть всю страницу панели.

    Фолбэк именно на СТРОКУ, а не на блок, потому что файл СМЕШАННЫЙ по
    кодировке — это не край, а прямое следствие Task 6: он ставит гардиану
    `-Encoding utf8`, и с этой минуты в одном файле лежит cp1251-прошлое и
    utf-8-будущее. Блочный фолбэк в такой день портит самое ценное: одна старая
    cp1251-байта роняет `decode("utf-8")` на ВСЁМ окне, и кракозябрами
    становятся СВЕЖИЕ строки, а старые читаются словами. Воспроизведено: панель
    назвала `yarina` фактом «из лога гардиана», пока свежайшая строка лога
    называла `volska`.

    Резать по `b"\\n"` безопасно в обеих кодировках: 0x0A не встречается ни
    внутри многобайтовой последовательности utf-8, ни как часть символа cp1251.
    Обратная склейка через `"\\n".join(...)` + `splitlines()` оставляет разбор
    на строки ровно таким, каким он был (CRLF из PowerShell, `\\r`, `\\u2028`).
    """
    p = Path(path)
    try:
        size = p.stat().st_size
        with open(p, "rb") as f:
            if size > limit:
                f.seek(size - limit)
                # Ровно `limit` байт, а не «до конца файла»: гардиан пишет в
                # этот лог ЖИВОЙ, и всё дописанное между stat() и read()
                # приехало бы сверх окна — окно перестало бы быть окном.
                raw = f.read(limit)
            else:
                raw = f.read()
    except OSError:
        return [], False

    truncated = size > limit
    if truncated:
        # Обрубок первой строки отрезается ДО декодирования, и порядок здесь
        # несущий. Срез по байтам рассекает не только строку (обрубок в ленте
        # выглядит как событие с потерянным началом), но и многобайтовый
        # СИМВОЛ: на половине кириллической буквы `decode("utf-8")` бросает
        # UnicodeDecodeError и уводит в cp1251 ВЕСЬ здоровый блок — из-за
        # одного разрубленного байта кракозябрами приезжает вся лента.
        # Отрезав обрубок байтами, мы убираем и половину символа вместе с ним.
        # Пустой хвост (в окно не попало ни одного перевода строки) — это
        # честный «ни одной целой строки не видно», а не молчаливая порча.
        raw = raw.partition(b"\n")[2]
    text = "\n".join(_decode_line(chunk) for chunk in raw.split(b"\n"))
    # BOM снимается ЗДЕСЬ, а не «когда-нибудь понадобится»: Task 6 ставит
    # гардиану `-Encoding utf8`, а PowerShell 5.1 под этим именем пишет utf-8
    # ИМЕННО С BOM (проверено: EF BB BF в начале файла). Незамеченный U+FEFF
    # приклеивается к первому символу первой строки, и `parse_log_ts` на ней
    # возвращает None — самая свежая запись ленты встаёт с прочерком.
    # Записан escape'ом намеренно: сам символ невидим и в diff'е не читается.
    text = text.lstrip("\ufeff")
    return text.splitlines(), truncated


# ───────────────── Лента: какую базу читаем (лестница фактов) ────────────────
#
# Панель и раннер — СИБЛИНГИ, а не родня: раннера поднимает
# `scripts/chatter_guardian_detached.ps1`, панель живёт в
# `scripts/run_backend_detached.py` под ДРУГИМ гардианом. Общего окружения у них
# нет вовсе — проверено 14.08 на живых PID: у раннера (6864/9456)
# CHATTER_PERSONAS='volska' и CHATTER_DB='.secrets\demo.db', у панели (8908) обе
# переменные None, и в реестре USER/MACHINE их тоже нет.
#
# Поэтому прежняя редакция, спрашивавшая СВОЁ окружение, была инертна: 14.08 во
# время демо Ярины она прочитала бы demo.db, пока раннер обслуживал yarina.db, —
# то есть чинила дефект ровно тем способом, которым он и возникал.
#
# Лестница ниже спрашивает сам раннер, а догадки называет вслух:
#   1. TAMAPI_DB / CHATTER_DB своего окружения — стенд или ручной запуск;
#   2. ЖИВОЙ раннер: `psutil.Process(pid).environ()` — ФАКТ (на этой машине
#      читается без повышения прав, проверено на живых PID);
#   3. лог гардиана: последняя строка «состав: … db=…» — вчерашний факт;
#   4. `active.yaml` — догадка о том, что раннер прочитал БЫ, стартуй он сейчас.
#
# Пустое пояснение = ответу можно верить (шаги 1–2). Непустое Task 5 ставит
# строкой ленты с источником «панель»: догадка, названная догадкой, — это не то
# же самое, что догадка, выданная за факт.
#
# `--db` в argv раннера панель НЕ разбирает: гардиан его не передаёт вовсе
# (`chatter_guardian_detached.ps1:226` запускает `-u -m chatter.telethon_run
# --llm real`), а ручной запуск с флагом останется невидимым — пробел названный,
# а не забытый.

# Что гардиан пишет вместо пути, когда CHATTER_DB не задан (ps1:145). Принять
# эту фразу за путь значит показать ленту файла с таким именем.
LOG_DB_UNSET = "по первому слагу"
# Строка состава из лога гардиана. Формат писателя — там же, ps1:145:
#   «состав: CHATTER_PERSONAS=<roster>, db=<путь|по первому слагу>»
#
# ЗАЯКОРЕНО с начала строки вместе с отметкой времени, и это ГРАБЛЯ 1 файла
# (запуск против упоминания) в логовом измерении. В тот же файл гардиан выливает
# ЧУЖОЙ текст дословно: `Invoke-WatchCheck FAILED: $($_.Exception.Message)`
# (ps1:166) и `Start-Process FAILED: …` (ps1:232) — сообщение исключения едет в
# лог как есть. Незаякоренный `.search()` находил «состав: …» ВНУТРИ такой
# строки, и пересказ дал панели путь `C:\zlo.db` (воспроизведено).
#
# Префикс времени берётся у `_LOG_TS_RE`, а не переписывается рядом: две записи
# одного формата разъехались бы молча, а разбирают они одну и ту же строку.
_LOG_ROSTER_RE = re.compile(
    _LOG_TS_RE.pattern + r"состав: CHATTER_PERSONAS=(?P<roster>.*?), db=(?P<db>.+)$")


def _abs_db(raw: str) -> str:
    """Путь раннера — в ЕГО системе координат: он держит `CHATTER_DB` вида
    `.secrets\\demo.db` при cwd `C:\\jarvis`. Панель поднимает другой гардиан, и
    её cwd может быть любым, поэтому относительный путь домысливается от ROOT, а
    не от текущего каталога процесса.

    `p.drive` проверяется ОТДЕЛЬНО от `is_absolute()`: Windows принимает
    диск-относительный путь `C:x.db` (относительно текущего каталога диска C:),
    а `Path("C:x.db").is_absolute()` при этом False — и такой путь приклеивался
    к ROOT, давая несуществующее имя `<ROOT>\\C:x.db`. Диск в пути назван —
    значит домысливать нечего."""
    p = Path(raw.strip().strip('"'))
    return str(p if p.is_absolute() or p.drive else ROOT / p)


def _db_from_slug(slug: str) -> str:
    """`.secrets/<slug>.db` — ровно то, что выводит сам раннер
    (`chatter/telethon_run.py:derive_db_path`). Равенство держит СТОРОЖ ПАРИТЕТА
    (`test_the_panel_derives_the_file_exactly_as_the_runner_does`), а не это
    предложение: смена схемы имён у раннера обязана красить тест, а не молча
    возвращать дефект.

    Равенство держится ещё и тем, что у раннера `SECRETS_DIR = Path(".secrets")`
    ОТНОСИТЕЛЬНЫЙ (`chatter/telethon_run.py:77`), а его cwd — это ROOT:
    `chatter_guardian_detached.ps1:229` запускает его с `-WorkingDirectory $Root`.
    Уедет любое из двух — панель начнёт читать файл, которого раннер не пишет,
    и сторож паритета обязан это поймать, поэтому каталог ему НЕ подсовывается."""
    return str(ROOT / ".secrets" / f"{slug}.db")


def _primary_slug(roster: str) -> str:
    """Первый НЕПУСТОЙ slug состава — ровно как у раннера
    (`chatter.config.active.resolve_personas`: `[s.strip() for s in ... if s.strip()]`).

    Сырой `split(",")[0]` расходился с ним на `CHATTER_PERSONAS=',volska'`:
    раннер обслуживает volska.db, а панель получала пустой slug и говорила
    «раннер жив, но базу в своём окружении не называет» — пояснение ЛОЖНОЕ,
    он её называл, читать не умели мы."""
    return next((s for s in (part.strip() for part in roster.split(",")) if s), "")


def _db_from_runner_env(env: dict) -> str | None:
    """Какую базу называет ОКРУЖЕНИЕ раннера. Порядок — как у него самого
    (`resolve_runtime_paths`: CHATTER_DB > вывод из первичного slug'а)."""
    db = (env.get("CHATTER_DB") or "").strip()
    if db:
        return _abs_db(db)
    slug = _primary_slug(env.get("CHATTER_PERSONAS") or "")
    return _db_from_slug(slug) if slug else None


def _db_from_live_runner(table: list[tuple] | None) -> tuple[str | None, str]:
    """(путь, почему не факт). Спрашиваем ЖИВОЙ раннер — единственный источник,
    который знает ответ наверняка, потому что базу он и обслуживает.

    Поиск процесса — тем же `_launches`, что у `processes()`: ГРАБЛЯ 1 файла
    (маркер в теле `-c` — разговор О раннере, а не раннер) стоила ложных выводов
    уже дважды, и второй способ искать процессы завёл бы её обратно.

    Раннеров штатно ДВА PID (лаунчер + сам сервис) — это норма фермы, а не
    двойник. Совпали ответы — берём; разошлись — живут две сессии на разных
    базах, и половина ленты будет не о том клиенте."""
    try:
        import psutil                    # ЛЕНИВО: панель не умирает без psutil
    except Exception:
        return None, "psutil недоступен — живой раннер не опрошен"

    snap = _proc_table() if table is None else table
    pids = [row[0] for row in snap
            if row[1].startswith("python") and _launches(CHATTER_RUNNER, row)]
    if not pids:
        return None, ""                  # пусто = «раннера нет», а не «не смогли»

    answers, denied, gone = [], [], []
    for pid in pids:
        try:
            env = psutil.Process(pid).environ() or {}
        except psutil.NoSuchProcess:
            # ШТАТНАЯ гонка, а не редкость: между `_proc_table()` и `environ()`
            # гардиан вправе перезапустить раннер (14.08 он сделал это за 61 с).
            # Слить её с AccessDenied значит сказать «раннер жив» о процессе,
            # которого в этот момент уже нет, — а это разные новости: одна про
            # права, другая про перезапуск.
            gone.append(str(pid))
        except Exception as exc:         # noqa: BLE001 — источник внешний
            # DEV-18: AccessDenied и прочее не глотаем. Ответ ниже по лестнице
            # всё равно будет, но он ДОГАДКА, и разница обязана доехать до
            # ленты словами.
            denied.append(f"PID {pid}: {type(exc).__name__}")
            continue
        else:
            db = _db_from_runner_env(env)
            if db is not None:
                answers.append((pid, db))

    if not answers:
        parts = []
        if denied:
            parts.append("раннер жив, но его окружение не прочитать "
                         f"({', '.join(denied)})")
        if gone:
            parts.append("раннер исчез, пока мы спрашивали "
                         f"(PID {', '.join(gone)})")
        if parts:
            return None, "; ".join(parts)
        return None, "раннер жив, но базу в своём окружении не называет"
    if len({db for _, db in answers}) > 1:
        listed = "; ".join(f"PID {pid} — {db}" for pid, db in answers)
        return answers[0][1], (f"живые раннеры называют РАЗНЫЕ базы ({listed}) "
                               "— взята первая, лента может быть не о том клиенте")
    return answers[0][1], ""


def _db_from_guardian_log() -> tuple[str | None, float | None, str]:
    """(путь, время строки, оговорка про окно) из последней строки «состав: … db=…».

    Читается тем же `read_tail`, что и лента: у растущего вечно лога второго
    способа чтения быть не должно.

    Берётся ПОСЛЕДНЯЯ подходящая строка и на ней разбор кончается: она написана
    текущим запуском гардиана, а всё, что выше, — прошлые составы. Если она
    называет `active.yaml`, лог не знает ничего сверх шага 4.

    Третьим элементом возвращается ОГОВОРКА, потому что `truncated` здесь
    несущий, а не декоративный: строку «состав» гардиан пишет ТОЛЬКО при своём
    старте (ps1:145), ротации у лога нет вовсе, и при долгом аптайме строка
    уезжает за окно. Молча промолчав, ступень 3 выглядела бы точно так же, как
    на машине, где гардиан не запускался никогда, — а это разные вещи."""
    lines, truncated = read_tail(ROOT / "logs" / "chatter_guardian.stdout.log")
    for line in reversed(lines):
        m = _LOG_ROSTER_RE.match(line)
        if not m:
            continue
        db = m.group("db").strip()
        if db and db != LOG_DB_UNSET:
            return _abs_db(db), parse_log_ts(line), ""
        # `volska (флаг)` — состав пришёл переменной; `active.yaml` — файлом.
        roster = m.group("roster").strip().split(" (")[0].strip()
        if roster and roster != "active.yaml":
            slug = _primary_slug(roster)
            if slug:
                return _db_from_slug(slug), parse_log_ts(line), ""
        return None, None, ""
    gap = ("лог гардиана длиннее окна чтения — строка состава могла не попасть в него"
           if truncated else "")
    return None, None, gap


def client_db_path(table: list[tuple] | None = None) -> tuple[str | None, str]:
    """(путь к базе клиентов, пояснение).

    Пустое пояснение означает «ответу можно верить»: он либо задан явно, либо
    взят у живого раннера. Непустое — ответ есть, но он выведен, и лента обязана
    сказать, откуда. `None` в пути — ответа нет вовсе.

    Путь ВСЕГДА абсолютный, на всех четырёх ступенях. Раньше ступень 1 отдавала
    `TAMAPI_DB`/`CHATTER_DB` как есть, а ступени 2–4 — абсолютный: два контракта
    в одном возврате, и потребитель разрешал относительный путь то от cwd панели
    (а он у неё какой угодно), то от ROOT.

    `table` — уже собранная таблица процессов (как у `processes`/`guardians`).
    Замер 15.08 в свежем процессе: `_proc_table()` тёплым = 8.6 мс,
    `client_db_path()` без готовой таблицы = 9.7 мс, с готовой = 0.8 мс. То
    есть экономия ≈9 мс, а не «~1 с», как здесь когда-то стояло, и не 591 мс:
    холодные 569 мс — это импорт psutil, его платит ПЕРВЫЙ обход в процессе, а
    он делается в любом случае. Таблицу строит `snapshot_fast()`, и до ленты её
    протягивает `snapshot_slow(table=...)` — но только на пути
    `/api/snapshot`: `panel()` и `/slow` ходят раздельно, и там `table=None`.

    Импорт `psutil` и `chatter.config.active` ЛЕНИВЫЙ и внутри функции —
    сломанное дерево chatter или отсутствующий psutil не имеют права уронить
    импорт панели фермы.

    DEV-18: ошибка чтения состава — ЯВНАЯ строка, а не тихий откат на demo.db;
    тихий откат и есть починяемый дефект.
    """
    env_db = os.getenv("TAMAPI_DB") or os.getenv("CHATTER_DB")
    if env_db:
        # Пустая строка — это «не выставлена», а не «база в файле с пустым
        # именем»: sqlite открыл бы такое имя без единой жалобы, и лента молча
        # опустела бы.
        return _abs_db(env_db), ""

    live_db, why = _db_from_live_runner(table)
    if live_db is not None:
        return live_db, why              # непусто только при расхождении раннеров
    reason = why or "раннер не запущен"

    log_db, log_ts, log_gap = _db_from_guardian_log()
    if log_db is not None:
        when = (time.strftime("%d.%m %H:%M", time.localtime(log_ts)) if log_ts
                else "неразобранного времени")
        return log_db, f"{reason}, база из лога гардиана от {when}"
    # Лог промолчал. Оговорка про окно едет вместе с ответом ступени 4: без неё
    # «строка состава уехала за окно» и «гардиан не стартовал ни разу» выглядят
    # в ленте одинаково.
    gap = f"; {log_gap}" if log_gap else ""

    clients_dir = ROOT / "chatter" / "clients"
    # Проверяем ДО вызова: `resolve_personas` при отсутствии файла ТИХО отдаёт
    # LEGACY_PERSONAS (['demo','demo2']) — страховка, осмысленная для раннера
    # (прод не падает на первом же рестарте) и ядовитая для панели, которая
    # выдала бы demo.db за прочитанный состав. Поведение раннера не трогаем,
    # молчать об этом — не имеем права.
    file_missing = not (clients_dir / "active.yaml").exists()
    try:
        from chatter.config.active import resolve_personas
        slugs = resolve_personas(clients_dir=clients_dir, env=os.environ)
    except Exception as exc:                       # noqa: BLE001 — источник внешний
        return None, (f"склад клиентов не прочитан ({type(exc).__name__}: {exc}) "
                      f"— какую базу читать, неизвестно")
    if not slugs:
        # Сегодня пустой состав `resolve_personas` не возвращает — она на нём
        # кричит ActiveClientsError. Но `slugs[0]` держится на этом обещании
        # ЧУЖОГО модуля, и в день, когда обещание изменится, страница фермы
        # упадёт IndexError'ом целиком вместо одной строки в ленте.
        return None, ("склад клиентов не прочитан (список клиентов пуст) "
                      "— какую базу читать, неизвестно")

    env_roster = (os.getenv("CHATTER_PERSONAS") or "").strip()
    if file_missing and not env_roster:
        return _db_from_slug(slugs[0]), (
            f"склад клиентов не найден, взято legacy-умолчание {slugs[0]} "
            f"— лента может быть не той базы{gap}")
    # CHATTER_PERSONAS в окружении ПАНЕЛИ говорит о панели, а не о раннере:
    # источник называем тот, который сработал на самом деле.
    src = "CHATTER_PERSONAS панели" if env_roster else "active.yaml"
    return (_db_from_slug(slugs[0]),
            f"{reason} и лог молчит — база выведена из {src}{gap}")


def _as_ts(value) -> float | None:
    """Время события ЧИСЛОМ — или ничего.

    sqlite типизирован динамически: `ts REAL NOT NULL` не мешает нечисловому
    тексту лечь в колонку как TEXT (проверено вставкой, `typeof(ts)` = 'text').
    Дальше такая строка ломала ВСЁ, что делает с временем арифметику: `-(ts)`
    в ключе сортировки и `now - ts` в разметке. Первое гасило ленту молча
    (`TypeError` ловил `_safe` медленной половины и подменял ленту пустотой),
    второе уронило бы страницу целиком.

    `bool` отсекается отдельно: он подкласс `int`, и `True` стал бы временем
    «01.01.1970 03:00:01» — выдуманным, а значит достоверным на вид.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


# Свежее — выше. Строки БЕЗ времени идут первыми: это не события прошлого, а
# состояние СЕЙЧАС (провал источника, граница видимости), и прятать его под
# вчерашние записи значит спрятать единственное, что требует действия.
#
# Кривое время приравнено к отсутствующему НЕ из вежливости к мусору: одна
# строка чужого источника не имеет права решать, увидит ли владелец ленту.
def _newest_first(e: dict) -> tuple:
    ts = _as_ts(e.get("ts"))
    return (ts is not None, -(ts or 0.0))


def _share_window(groups: list[list[dict]], budget: int) -> list[dict]:
    """Окно ленты, поделённое между источниками.

    ЗАМЕР ДО ПРАВКИ на живых источниках: в ленте 40 строк, из них 35 —
    control_events базы и только 5 — гардиан (2 из этих 5 дебаунсные). Причина
    была НЕ в фильтре и не в том, что клиент свежее: обе пачки складывались в
    ОДИН список в порядке `append` и резались общим `limit`. Эту причину
    сортировка по времени уже сняла, и замер «35 против 5» её больше не
    доказывает — держать его как обоснование квоты значит опираться на число,
    которое говорит о другом.

    ЗАЧЕМ ТОГДА КВОТА — И ЧЕМ ЭТО ДОКАЗАНО. Решение владельца 15.08: квоту
    оставляем, но опирается она на СТОРОЖА, а не на замер. Доказательство —
    `test_the_quota_keeps_the_other_source_visible_under_a_flood`: стенд там
    построен так, что чистая сортировка по свежести обязана вытеснить второй
    источник ЦЕЛИКОМ (каждая строка потока свежее любой строки соседа, и одного
    потока хватает на всё окно), проверка идёт В ОБЕ стороны и НА РАЗМЕТКЕ, а
    оба источника превышают свою долю — то есть «доля» и «влезло всё» там
    различимы. Снятие квоты, перекос квоты в любую сторону и подмена доли
    полным окном роняют его порознь.

    Замер 14.08 годится только как напоминание, что свежее бывает то один
    источник, то другой: свежайшее control_events было 13.08, свежайшая строка
    гардиана — 14.08, клиент старше на ~22 часа, и за срез уезжал КЛИЕНТ
    (5 строк из 20 на экране). Какой источник спрячется без квоты — вопрос
    сегодняшней погоды, а не устройства ленты.

    ПРИНЯТОЕ РЕШЕНИЕ: каждому источнику ГАРАНТИРОВАННАЯ доля окна
    (`budget // число источников`), а всё, чего источник не добрал, достаётся
    соседям в порядке этого списка. То есть молчащий гардиан не тратит окно
    впустую (обычный день — это лента чата целиком), а говорящий не может быть
    заглушён потоком клиентских событий.

    Оба конца закреплены ПАРОЙ сторожей (`..._does_not_starve_the_guardian` /
    `..._does_not_starve_the_client`): односторонняя проверка зеленела бы на
    перекосе в другую сторону, и лента снова показывала бы один источник.

    Каждая группа обязана приехать сюда уже отсортированной: доля отрезается с
    её начала, и «первые N» должны означать «самые свежие N».
    """
    if budget <= 0 or not groups:
        return []
    # Без `max(1, …)`: при бюджете меньше числа групп подпорка выдавала КАЖДОЙ
    # группе по строке и отдавала БОЛЬШЕ бюджета (`budget=1` → 2 строки).
    # Наружу это маскировал финальный `out[:limit]` в `events()`, а съедала
    # маскировка как раз строки ВНЕ бюджета — те, что говорят про сейчас.
    # Раздача остатка ниже сама разберёт бюджет, включая нулевую квоту.
    quota = budget // len(groups)
    taken = [g[:quota] for g in groups]
    spare = budget - sum(len(t) for t in taken)
    for group, part in zip(groups, taken):
        if spare <= 0:
            break
        extra = group[len(part):len(part) + spare]
        part.extend(extra)
        spare -= len(extra)
    return [row for part in taken for row in part]


# Размер окна ленты. ОДНО число на всю дорогу от sqlite до экрана.
#
# Раньше их было два: `events(limit=40)` делила бюджет между источниками, а
# разметка добавляла свой `events[:25]` — уже ПОСЛЕ общей сортировки, то есть
# чисто по свежести. Второй срез сводил дележ окна на нет: на живых источниках
# `events()` отдавала 20 строк гардиана и 20 клиента, а на экране оставалось 20
# гардиана и 5 клиента; на пропорции «свежие клиентские против старых
# гардианских» гардиана на экране не оставалось вовсе.
#
# 25 — ровно то, что экран показывает сегодня: размер первого экрана эта правка
# не меняет, она убирает ВТОРОЕ число.
FEED_LIMIT = 25

# Предел длины детали — ОДИН на оба источника: колонка «Деталь» на экране одна.
# Строки гардиана резались всегда, клиентские ехали целиком (замерено: 5000
# символов доезжали до сортировки).
FEED_DETAIL_LIMIT = 120


def events(limit: int = FEED_LIMIT, table: list[tuple] | None = None) -> list[dict]:
    """Лента: control_events клиента + решения гардиана chatter.

    Четыре правки 14.08 (спека §5) — все четыре готовыми функциями, а не
    заново:
      · база берётся у `client_db_path`, а не литералом `.secrets/demo.db`;
      · строки гардиана несут ВРЕМЯ (`parse_log_ts`), и лента сортируется по нему;
      · читается хвост лога (`read_tail`), а не весь растущий файл, и граница
        видимости называется вслух;
      · дебаунс-шум сторожа отсеивает `is_decision`, а не суп из подстрок.

    `table` — уже собранная таблица процессов: её строит `snapshot_fast()`, а
    лента живёт в `snapshot_slow()`. Экономия ЧЕСТНАЯ, но маленькая: замер
    15.08 в свежем процессе — `client_db_path` с готовой таблицей 0.8 мс, без
    неё 9.7 мс ТЁПЛЫМ, то есть ≈9 мс, один раз на `/api/snapshot`.

    ⚠️ 591 мс, которые здесь стояли, к этому обходу отношения не имеют. Второй
    обход тёплый ПО ПОСТРОЕНИЮ: холодная цена — это импорт psutil (замер 15.08:
    первый обход в свежем процессе 569 мс, тёплый 8.6 мс), и платит её первый
    обход, который делается в любом случае. До человека экономия к тому же не
    доходит вовсе: страницу рисует `panel()` через `snapshot_fast()`, а `/slow`
    идёт отдельным запросом с `table=None` намеренно.

    ЦЕНА САМОЙ ЛЕНТЫ: `events(table=готовая)` — 4.1 мс против 0.8 мс у старой
    редакции. Подорожание настоящее и названо здесь нарочно: за него куплены
    хвост лога вместо `read_text()`, время каждой решающей строки (`strptime`)
    и разбор состава. На фоне 1.74 с полного снапшота это не регрессия первого
    экрана — первый экран ленту не собирает вовсе.

    Сторож на то, что аргумент действительно доезжает, —
    `test_a_ready_process_table_is_not_rebuilt`.

    «Посчитано ≠ доехало» — доставку алертов мы сегодня не журналируем, и это
    помечено как пробел в самой разметке.
    """
    # Строки О САМОЙ ЛЕНТЕ: провал источника и граница видимости. Они стоят ВНЕ
    # бюджета, потому что говорят про СЕЙЧАС, — попади они в общую очередь на
    # равных, поток свежих событий вытеснил бы аварию конфигурации, и она
    # выглядела бы тишиной (DEV-18).
    # `LIMIT -1` в sqlite означает «БЕЗ ПРЕДЕЛА»: словари строились бы по всей
    # таблице `control_events` и тут же выбрасывались срезом. Тихо и дорого.
    limit = max(0, limit)

    fixed: list[dict] = []
    client: list[dict] = []
    guard: list[dict] = []

    db, db_note = client_db_path(table)
    if db_note:
        # `if`, а НЕ `elif`: по лестнице `client_db_path` непустое пояснение
        # приходит ВМЕСТЕ с рабочим путём — ступени 3–4 отвечают догадкой и
        # честно её называют. При `elif` панель показала бы одну строку-
        # пояснение и пустую ленту: тишину там, где данные есть.
        fixed.append({"src": "панель", "kind": "склад клиентов",
                      "detail": db_note, "ts": None})
    if db:
        try:
            from app.services.tamapi_metrics import _ro
            with _ro(db) as c:
                for r in c.execute(
                        "SELECT kind, contact_id, detail, ts FROM control_events "
                        "ORDER BY id DESC LIMIT ?", (limit,)):
                    client.append({"src": "chatter", "kind": r["kind"],
                                   "detail": (r["detail"] or r["contact_id"]
                                              or "")[:FEED_DETAIL_LIMIT],
                                   # Нормализуем НА ИСТОЧНИКЕ, а не только в
                                   # ключе сортировки: ниже по течению время
                                   # берёт ещё и разметка (`_ago`), и чинить
                                   # тихую пустую ленту громким 500 незачем.
                                   "ts": _as_ts(r["ts"])})
        except Exception as exc:                   # noqa: BLE001 — источник внешний
            # DEV-18: провал источника виден В САМОЙ ленте, а не в тишине.
            # Прежний `except Exception: pass` делал отсутствующий файл базы
            # неотличимым от «клиент сегодня молчал».
            fixed.append({"src": "панель", "kind": "база клиентов",
                          "detail": f"{db}: {type(exc).__name__}: {exc}", "ts": None})

    lines, truncated = read_tail(ROOT / "logs" / "chatter_guardian.stdout.log",
                                 GUARDIAN_LOG_WINDOW)
    kept = [ln for ln in lines if is_decision(ln)]
    for ln in kept:
        guard.append({"src": "гардиан", "kind": "раннер",
                      # Префикс времени вырезается: оно уехало в свою колонку
                      # («Когда» в `_events_table`), и дублировать его в узкой
                      # колонке детали значит занять её уже нарисованным.
                      "detail": _LOG_TS_RE.sub("", ln)[:FEED_DETAIL_LIMIT],
                      "ts": parse_log_ts(ln)})
    if truncated:
        oldest = next((parse_log_ts(ln) for ln in kept if parse_log_ts(ln)), None)
        seen_from = (time.strftime("%d.%m %H:%M", time.localtime(oldest))
                     if oldest else "неизвестного момента")
        fixed.append({"src": "гардиан", "kind": "граница видимости",
                      # `ts=None`, а НЕ `oldest`: строка говорит про СЕЙЧАС —
                      # «дальше вглубь панель не видит». Подписанная самым
                      # старым временем ленты, она после общей сортировки
                      # уезжала в самый низ, и на стенде с непустой лентой её
                      # на экране не было вовсе. Момент начала видимости
                      # остаётся в ТЕКСТЕ — он и есть содержание строки.
                      "detail": f"лог длиннее окна: видно с {seen_from}",
                      "ts": None})

    client.sort(key=_newest_first)
    guard.sort(key=_newest_first)
    out = fixed + _share_window([client, guard], limit - len(fixed))
    out.sort(key=_newest_first)
    return out[:limit]


def api_keys() -> list[dict]:
    """ТОЛЬКО метаданные: имя, назначение, срок, здоровье. Значения не читаются,
    не расшифровываются и не маскируются — их здесь просто нет."""
    known = [
        {"name": "Instagram (Path B)", "purpose": "публикация/лента IG",
         "expires": "2026-09-09", "auto": "JarvisIgTokenRefresh",
         "note": "хвост: сбросить IG App Secret (светился)"},
        {"name": "ANTHROPIC_API_KEY", "purpose": "brain + классификатор",
         "expires": None, "auto": None, "note": "основная трата"},
        {"name": "FAL_KEY", "purpose": "FLUX.2 генерация",
         "expires": None, "auto": None, "note": "⚠️ требует ротации"},
        {"name": "WaveSpeed", "purpose": "анимация",
         "expires": None, "auto": None, "note": "⚠️ утекал — ротировать"},
        {"name": "OpenAI", "purpose": "voice", "expires": None, "auto": None, "note": ""},
        {"name": "Telegram (бот + пульт)", "purpose": "каналы управления",
         "expires": None, "auto": None, "note": "разные токены"},
    ]
    today = time.gmtime()
    for k in known:
        if k["expires"]:
            try:
                exp = time.strptime(k["expires"], "%Y-%m-%d")
                days = int((time.mktime(exp) - time.mktime(today)) / 86400)
                k["days_left"] = days
                k["state"] = "bad" if days < 7 else ("warn" if days < 30 else "ok")
            except ValueError:
                k["days_left"], k["state"] = None, "warn"
        else:
            k["days_left"] = None
            k["state"] = "warn" if "⚠️" in (k["note"] or "") else "ok"
    return known


def external_watchdog() -> Row:
    """§0 спеки: панель обязана говорить о собственной слепоте. Пока внешний
    сторож не настроен — красная строка на самом видном месте."""
    url = (os.getenv("HEALTHCHECKS_URL") or "").strip()
    if not url:
        return Row("ext", "Внешний сторож", "bad",
                   "НЕ настроен — панель не видит смерти самой машины")
    state_file = ROOT / "state" / "healthchecks_last.txt"
    age = None
    try:
        age = _now() - state_file.stat().st_mtime
    except OSError:
        pass
    if age is None:
        return Row("ext", "Внешний сторож", "warn",
                   "URL задан, но пингов ещё не было")
    if age > 300:
        return Row("ext", "Внешний сторож", "warn",
                   f"последний пинг {int(age // 60)} мин назад")
    return Row("ext", "Внешний сторож", "ok", f"пинг {int(age)} с назад")


def is_anomaly(kind: str, item) -> bool:
    """Единственная точка решения «аномалия или состояние».

    Аномалия = то, что может изменить моё поведение в ближайший час. Возраст
    ветки растёт сам собой, а «heartbeat 12 с тому» меняется на каждый запрос,
    не меняя смысла ни разу — это состояние, и его место в свёртке.

    `external` не проходит ни по одной ветке НАМЕРЕННО: «внешний сторож не
    настроен» истинно всегда, и в аномалиях оно каждый день требовало бы
    внимания, которого не заслуживает. Строка о нём рисуется отдельной note —
    видимой, но не претендующей ни на ответ, ни на список аномалий.
    """
    if kind in ("process", "guardian"):
        return item.state != "ok"
    if kind == "task":
        # off — это RETIRED: отставлено осознанно, чинить нечего.
        return item.state not in ("ok", "off")
    if kind == "key":
        days = item.get("days_left")
        return (days is not None and days < KEY_EXPIRY_ANOMALY) or "⚠️" in (item.get("note") or "")
    if kind == "arc":
        # Грязное дерево слепит мерж-гейт (сторож крутил 1669 циклов вхолостую).
        # Возраст и «не смержена» — состояние: они не про ближайший час.
        return bool(item.get("dirty"))
    return False


def snapshot_fast(table: list[tuple] | None = None) -> dict:
    """Миллисекунды: psutil + два `stat`. Этого достаточно для ответа сверху.

    Разрез появился потому, что полный снапшот собирается 6.8 с (git по 21
    worktree + PowerShell), и первый экран платил их целиком — при том что
    ответ «что происходит и надо ли бежать» из медленной части почти не
    зависит.

    `table` принимается для `snapshot()`: он собирает обе половины разом, и
    обход процессов у него был двойным."""
    if table is None:
        table = _proc_table()      # ОДИН обход процессов на оба сборщика
    return {
        "collected_at": _now(),
        "external": external_watchdog(),
        "processes": processes(table),
        "guardians": guardians(table),
    }


# Кэш медленной части в памяти процесса. Не файл и не таблица: заход 1 не
# заводит новых сущностей, а переживать рестарт бэкенда этому кэшу незачем.
_slow_cache: dict | None = None


def slow_cached(*, max_age: float = SLOW_TTL) -> dict | None:
    """Медленная часть, ТОЛЬКО если она свежая. Ничего не собирает.

    None означает «не прочитано» — и экран обязан сказать это словами. Молча
    показать ноль задач и ноль ключей значит соврать ровно в том случае, ради
    которого панель существует."""
    snap = _slow_cache
    if snap and (_now() - snap["collected_at"]) < max_age:
        return snap
    return None


def snapshot_slow(*, force: bool = False,
                  table: list[tuple] | None = None) -> dict:
    """Медленная часть: PowerShell, git по всем worktree, sqlite, ключи.

    Каждый источник изолирован: упавший git не имеет права унести с собой
    задачи и ключи, а тем более — страницу целиком (DEV-18: провал видно в
    самой секции, а не в тишине).

    `table` — таблица процессов, собранная быстрой половиной, и она едет
    насквозь в `events`: лента спрашивает у живого раннера его базу, а второй
    обход процессов стоит ≈9 мс ТЁПЛЫМ (замер 15.08). Холодных 569 мс здесь не
    возникает: их платит первый обход, и он делается в любом случае.

    ⚠️ Ручка `/slow` зовётся отдельным запросом и быстрой половины при себе не
    имеет — там `None` честен, лента соберёт таблицу сама. То есть эти ≈9 мс
    достаются ТОЛЬКО `/api/snapshot`: человеческий путь (`panel()` + `/slow`)
    их не получает."""
    global _slow_cache
    if not force:
        fresh = slow_cached()
        if fresh is not None:
            return fresh

    def _safe(fn, fallback):
        try:
            return fn()
        except Exception as e:                       # noqa: BLE001 — источник внешний
            return fallback(f"{type(e).__name__}: {e}")

    snap = {
        "collected_at": _now(),
        "tasks": _safe(scheduled_tasks,
                       lambda m: [Row("tasks", "плановые задачи", "warn", f"не прочитано: {m}")]),
        "arcs": _safe(arcs, lambda m: []),
        # Фолбэк ленты ВИДИМЫЙ, как у `tasks`: пустой список означал бы на
        # экране «сегодня тихо», то есть тишину ровно там, где сборка ленты
        # развалилась (DEV-18). Строка без времени — она про СЕЙЧАС.
        "events": _safe(lambda: events(table=table),
                        lambda m: [{"src": "панель", "kind": "лента",
                                    "detail": f"не собрана: {m}", "ts": None}]),
        "keys": _safe(api_keys, lambda m: []),
    }
    _slow_cache = snap
    return snap


def snapshot() -> dict:
    """Полный снапшот одним куском — для `/api/snapshot` и совместимости.

    Обход списка процессов делается ЗДЕСЬ и ровно один: раньше его платили
    дважды — сначала `snapshot_fast`, потом лента внутри `client_db_path`.

    ПОРЯДОК ПОЛОВИН НЕСУЩИЙ. Быстрая идёт первой, потому что это она
    подписывает таблицу процессов временем (`collected_at = _now()`). Собрав
    таблицу, отдав ей 6.8 с медленной половины и только потом поставив подпись
    «сейчас», ручка называла «раннер жив» процесс, умерший семь секунд назад
    (замер: зазор 2.39 с против 0.01 с). Медленная половина получает ТУ ЖЕ
    таблицу — второго обхода тут нет и не должно быть."""
    table = _proc_table()
    fast = snapshot_fast(table)
    slow = snapshot_slow(force=True, table=table)
    return {**fast,
            **{k: v for k, v in slow.items() if k != "collected_at"},
            "slow_collected_at": slow["collected_at"]}
