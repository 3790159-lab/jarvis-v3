# -*- coding: utf-8 -*-
"""DEV-58: сверка защиты кодировки у задач Планировщика.

Задача Планировщика, запускающая python, пишет диагностику в stdout, который
Планировщик НЕ сохраняет. Причину отказа читают, воспроизводя прогон руками, —
и если вывод при этом рвётся консольной кодировкой, причина теряется ВТОРОЙ
раз, ровно в аварии. За сутки 23.08 это случилось дважды: код возврата 2 у
ночного дрила без единого слова о причине, и утраченная соль в DEV-57.

Лечение — `-X utf8` в аргументах действия задачи. НЕ обёртка: прослойка умеет
проглотить код возврата и потерять аргументы. НЕ переменная окружения: она
действует незаметно и на всё сразу, включая ручные прогоны.

ВТОРАЯ ФОРМА (DEV-59). Половина парка запускается не питоном, а PowerShell, и
`-X utf8` про эти задачи не говорит ничего. Их признак другой: `.ps1` сам
объявляет кодировку консоли присваиванием `[Console]::OutputEncoding`.
`chcp 65001` признаком НЕ становится ни при каких условиях — про него уже
записано, что он врёт: показывает 65001 там, где поток декодируется иначе.
Признак, который можно удовлетворить враньём, сторожем не является.

Признаки разные, потому что и вещи разные: `-X utf8` правит то, ЧЕМ питон
кодирует свой вывод, а `[Console]::OutputEncoding` — то, ЧЕМ консоль его
декодирует. Один другого не заменяет; поэтому вид защиты сверяется с тем, чем
задача запускается, и расхождение получает своё состояние (`kind_mismatch`).

ЗДЕСЬ ЖИВЁТ ТОЛЬКО СВЕРКА и ничего больше. Опрос живой системы — отдельно и
снаружи: снимок ВНЕДРЯЕТСЯ. Сверка, ходящая в планировщик сама, проверялась бы
только на машине с этим планировщиком, то есть была бы стендом, а не сторожем.
Ровно поэтому и содержимое `.ps1` модуль не читает сам: ему ВНЕДРЯЮТ читателя
(`read_script`), путей он не строит и про диск не знает. Решение о том, что
считать объявленной кодировкой, при этом остаётся ЗДЕСЬ, под гейтом, а не
уезжает в непокрытый сборщик снимка.

Модуль НЕ импортирует `os`, `pathlib`, `subprocess` и не открывает файлов.
Это не гигиена, а предмет: он зовёт то, что ему дали.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Optional

# ── Ожидание. ЛИТЕРАЛЬНОЕ и в ОБЕ стороны ───────────────────────────────────
#
# Список не выводится из системы намеренно: выведенный согласен с системой по
# определению и промолчит ровно там, где задачу завели и забыли. Поэтому
# сверка идёт в обе стороны, и `unexpected` — не побочный случай, а главный:
# это та самая завтрашняя задача.
PROTECTION_X_UTF8 = "x_utf8"          # python-задача: `-X utf8` в аргументах
PROTECTION_PS_CONSOLE = "ps_console"  # .ps1 объявляет кодировку консоли
PROTECTION_EXEMPT = "exempt"          # намеренно вне арки, причина обязательна

# Ровно 13 имён — решение владельца 24.08 (контракт DEV-59 §7). Исключений по
# построению у парка НЕТ: `run_sniper_detached.ps1` и
# `infra_restart_cloudflared_action.ps1` объявляют кодировку наравне со всеми,
# хотя первая задача отставлена, а второй файл чисто ASCII.
TASK_ENCODING_EXPECTATION: dict = {
    "JarvisStateBackup": PROTECTION_X_UTF8,
    "JarvisRestoreDrill": PROTECTION_X_UTF8,
    "JarvisChatterCacheDigest": PROTECTION_X_UTF8,
    "JarvisDrillNightly": PROTECTION_X_UTF8,
    "JarvisIgTokenRefresh": PROTECTION_EXEMPT,
    "JarvisBackendGuardian": PROTECTION_PS_CONSOLE,
    "JarvisBotGuardian": PROTECTION_PS_CONSOLE,
    "JarvisChatterGuardian": PROTECTION_PS_CONSOLE,
    "JarvisHealthchecksPing": PROTECTION_PS_CONSOLE,
    "JarvisInfraRestartCloudflared": PROTECTION_PS_CONSOLE,
    "JarvisOpsWatchdog": PROTECTION_PS_CONSOLE,
    "JarvisPanelClientGuardian": PROTECTION_PS_CONSOLE,
    "JarvisSniperDetached": PROTECTION_PS_CONSOLE,
}

# ОЖИДАЕМОЕ КРАСНОЕ, названное заранее: на живой машине восемь `ps_console`
# задач сегодня дадут `unprotected` — `[Console]::OutputEncoding` в этих
# файлах ещё нет. Сторож называет работу, которая идёт следующим шагом и в
# строгом порядке (объявить -> рестарт -> приёмка), а не докладывает о беде.

# Причина исключения — МАШИНОЧИТАЕМАЯ и отдельным словарём.
#
# Отдельным, а не полем в таблице выше: таблица обязана остаться простым
# отображением «имя -> защита», иначе её нельзя прочитать одним взглядом. А
# исключение БЕЗ причины через месяц неотличимо от потерянной строки — и тогда
# следующий читатель не узнает, забыли её или вынесли осознанно. Ровно поэтому
# `exempt` без записи здесь больше НЕ зелёный (контракт §6.6).
EXEMPT_REASONS: dict = {
    "JarvisIgTokenRefresh":
        "решение владельца 24.08: вне арки DEV-58, разбирается отдельно",
}

# ── Состояния. СЕМЬ, и ни одно не склеивается с другим ──────────────────────
STATE_OK = "ok"                    # ожидается защищённой и защищена
STATE_UNPROTECTED = "unprotected"  # ожидается защищённой, признака нет
STATE_EXEMPT = "exempt"            # намеренно вне арки
STATE_MISSING = "missing"          # есть в ожидании, в системе НЕТ
STATE_UNEXPECTED = "unexpected"    # есть в системе, в ожидании НЕТ
STATE_UNREADABLE = "unreadable"    # признак НЕДОКАЗУЕМ -> красное
STATE_KIND_MISMATCH = "kind_mismatch"  # вид защиты не совпал с исполнителем

# `missing` и `unexpected` — РАЗНЫЕ вещи с разными действиями: первое чинится
# разбором, куда делась задача, второе — дописыванием строки в ожидание.
# Склеить их в одно «расхождение» значит потерять ответ на вопрос «что делать».
#
# `unreadable` не склеивается с `unprotected` по той же причине и ещё по
# одной: «недоказуем» — это НЕ «прошёл». Сторож, который зеленеет оттого, что
# ресурса нет, зелен по построению; это уже стоило нам одного молчаливого
# сигнала. Здесь чинят ШОВ (читателя, строку запуска, исполнителя), а в
# `unprotected` — сам файл.
#
# `kind_mismatch` отдельно от `unprotected`: там чинят строку запуска, а здесь
# разбираются, кто и зачем переписал действие задачи на другой исполнитель.
# Признак, который мы проверяем, стал не про эту задачу вовсе.

# ── Причины. ТОНЬШЕ состояния, и это тоже предмет ───────────────────────────
#
# `reason` есть на КАЖДОМ вердикте, включая зелёный. Там, где внутри одного
# состояния действия РАЗНЫЕ, различается и причина: иначе дедуп по причине не
# увидит смены одного красного на другое красное и промолчит ровно в тот день,
# когда картина поменялась.
REASON_OK = "ok"
REASON_EXEMPT = "exempt"
REASON_MISSING = "missing"
REASON_UNEXPECTED = "unexpected"
REASON_UNPROTECTED = "unprotected"
REASON_DECLARED_LATE = "declared_late"
REASON_KIND_MISMATCH = "kind_mismatch"
REASON_NO_READER = "no_reader"
REASON_READER_FAILED = "reader_failed"
REASON_ARGUMENTS_UNPARSED = "arguments_unparsed"
REASON_NO_ARGUMENTS = "no_arguments"
REASON_EXECUTE_UNKNOWN = "execute_unknown"
REASON_DECODE_FAILED = "decode_failed"
REASON_DUPLICATE_IN_SNAPSHOT = "duplicate_in_snapshot"
REASON_UNKNOWN_PROTECTION = "unknown_protection"
REASON_EXEMPT_WITHOUT_REASON = "exempt_without_reason"

# `declared_late` живёт под `unprotected`, а не отдельным состоянием,
# осознанно: действие одно и то же — править ФАЙЛ. Но правка разная (дописать
# против переставить), поэтому разницу видно машинно.


@dataclass(frozen=True)
class TaskVerdict:
    """Вердикт по одной задаче.

    `reason` присутствует ВСЕГДА, в том числе на зелёном, — как у проб в
    `ops_watchdog`. Иначе состояния неразличимы машинно.

    Граница вслух: `reason` — машиночитаемый КЛЮЧ, человеческий текст (тип
    исключения читателя, путь, номера строк) живёт в `detail`.
    """
    task: str
    state: str
    reason: str
    detail: str = ""


# ── Первая форма: `-X utf8` в строке аргументов ─────────────────────────────

# Отрезаем закавыченные куски ПЕРЕД поиском: путь вида
# "C:\что-то\-X utf8\run.py" не является защитой, хотя подстрока в нём есть.
_QUOTED = re.compile(r'"[^"]*"')

# Границы с ОБЕИХ сторон: слева нельзя лишний дефис (`--X utf8`), справа —
# лишняя буква (`-X utf8x`). Пробелов между `-X` и `utf8` может быть сколько
# угодно: это оформление, а не смысл.
_X_UTF8 = re.compile(r"(?<![\w-])-X\s+utf8(?![\w])")


def has_x_utf8(arguments: str) -> bool:
    """Несёт ли строка аргументов настоящий `-X utf8`."""
    return bool(_X_UTF8.search(_QUOTED.sub(" ", arguments or "")))


# ── Вторая форма, шаг 1: какой файл вообще исполняется ──────────────────────

# Флаги, после которых `-File` уже не флаг: всё, что идёт за `-Command` и
# `-EncodedCommand`, PowerShell считает ТЕЛОМ команды, а не своими ключами.
_PS_COMMAND_FLAGS = ("-command", "-encodedcommand")


def _split_arguments(arguments: str) -> list:
    """Строка запуска -> [(текст токена без кавычек, был ли он закавычен)].

    Кавычки снимаются, но факт закавыченности СОХРАНЯЕТСЯ: без него путь
    вида "C:\\a -File b\\x.ps1" притворился бы флагом — та же оговорка, что
    уже сделана в `has_x_utf8` для `-X utf8` внутри путей.
    """
    out: list = []
    token: list = []
    quoted = False
    started = False
    in_quotes = False

    for ch in (arguments or ""):
        if ch == '"':
            in_quotes = not in_quotes
            quoted = True
            started = True
            continue
        if ch.isspace() and not in_quotes:
            if started:
                out.append(("".join(token), quoted))
            token, quoted, started = [], False, False
            continue
        token.append(ch)
        started = True

    if started:
        out.append(("".join(token), quoted))
    return out


def script_path_from_arguments(arguments: str) -> Optional[str]:
    """Путь из явного `-File <путь>` строки запуска, иначе None.

    Понимается ТОЛЬКО явный `-File`; регистр флага свободен, кавычки вокруг
    пути необязательны и снимаются. Сокращения (`-f`, `-fi`) НЕ понимаются
    осознанно: живая форма у всех восьми задач одна, а угадывание сокращений
    есть источник ложного зелёного.

    `-Command`, `-EncodedCommand`, отсутствие `-File`, пустая строка и `None`
    дают None — и задача получает `unreadable`, а не зелёное: мы не знаем,
    какой файл проверять, и признать это вслух дешевле, чем проверить не тот.

    ХВОСТ после пути в путь не попадает: `JarvisPanelClientGuardian` живёт с
    `... -File "...\\panel_client_guardian_detached.ps1" -Slug yarina`.

    `-File` ВНУТРИ кавычек флагом не считается: строка
    `-File "C:\\dir -File fake.ps1\\real.ps1"` даёт весь закавыченный путь
    целиком.

    Нормализацией путей модуль не занимается намеренно: нормализация — это уже
    знание о диске, а его здесь нет.
    """
    tokens = _split_arguments(arguments)
    for idx, (text, was_quoted) in enumerate(tokens):
        if was_quoted:
            continue
        low = text.lower()
        if low in _PS_COMMAND_FLAGS:
            return None
        if low == "-file":
            if idx + 1 >= len(tokens):
                return None  # флаг есть, значения нет — разбирать нечего
            return tokens[idx + 1][0] or None
    return None


# ── Вторая форма, шаг 2: тот ли текст мы читаем, что исполняется ────────────

_UTF8_BOM = b"\xef\xbb\xbf"


def decode_ps1_bytes(data: bytes) -> str:
    """Байты `.ps1` -> текст ПО ПРАВИЛУ Windows PowerShell 5.1.

    BOM есть -> utf-8 (BOM отбрасывается); BOM нет -> cp1251. Правило не наше,
    а интерпретатора: читай сторож всегда как utf-8, он проверял бы не тот
    текст, который исполняется. Из восьми живых `.ps1` BOM есть у пяти, нет у
    трёх — ветка без BOM не гипотетическая, она живая.

    Байты, не декодируемые по своему правилу (в cp1251 не определён 0x98),
    поднимают ИСКЛЮЧЕНИЕ. Оно ловится в `compare_tasks` и даёт `unreadable` /
    `decode_failed`, а не зелёное: «не прочитали» — это не «прошло».
    """
    if data.startswith(_UTF8_BOM):
        return data[len(_UTF8_BOM):].decode("utf-8")
    return data.decode("cp1251")


def strip_ps_comments(source: str) -> str:
    """Погасить комментарии и строковые литералы, СОХРАНИВ позиции символов.

    Намеренная слепота — тот же класс, что дал нам AST-сторож одобренного
    текста, и здесь она load-bearing ДВАЖДЫ: и в поиске присваивания, и в
    поиске первой печати. У `ops_watchdog_detached.ps1` слово `Write-Host`
    текстом стоит на строке 17, а `param(` — на 20; читай сторож комментарии
    буквально, требование «объявить раньше печати» стало бы НЕВЫПОЛНИМЫМ, и
    правильная правка выглядела бы как красная.

    Погашенное заменяется пробелами, переводы строк остаются на местах: длина
    и разбивка на строки обязаны совпасть с исходником, иначе и «раньше первой
    печати», и номера строк в вердикте соврут.

    Один проход слева направо с состояниями: обычный текст / одинарные кавычки
    / двойные кавычки / `#` до конца строки / блочный комментарий / here-string
    обеих форм.
    """
    text = source or ""
    out: list = []
    i, n = 0, len(text)

    def blank(start: int, end: int) -> None:
        for ch in text[start:end]:
            out.append("\n" if ch == "\n" else " ")

    while i < n:
        ch = text[i]

        if text.startswith("<#", i):
            # Блочные комментарии PowerShell НЕ вкладываются: первая
            # закрывающая последовательность закрывает.
            end = text.find("#>", i + 2)
            end = n if end < 0 else end + 2
            blank(i, end)
            i = end
            continue

        if ch == "#":
            # Строчный комментарий до конца строки. Сюда попадаем только вне
            # кавычек: `"цена #5"` съедается веткой двойных кавычек целиком.
            end = text.find("\n", i)
            end = n if end < 0 else end
            blank(i, end)
            i = end
            continue

        if ch == "@" and i + 1 < n and text[i + 1] in ("'", '"'):
            # Here-string обеих форм. Закрывающая последовательность признаётся
            # только В НАЧАЛЕ строки — так её видит и сам PowerShell.
            term = text[i + 1] + "@"
            nl = text.find("\n", i)
            end = n
            if nl >= 0:
                k = nl + 1
                while k <= n:
                    if text.startswith(term, k):
                        end = k + 2
                        break
                    nxt = text.find("\n", k)
                    if nxt < 0:
                        break
                    k = nxt + 1
            blank(i, end)
            i = end
            continue

        if ch == "'":
            # Одинарные кавычки: единственный экран — удвоение. Двойная кавычка
            # внутри одинарных строку не рвёт.
            j = i + 1
            while j < n:
                if text[j] == "'":
                    if j + 1 < n and text[j + 1] == "'":
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            blank(i, j)
            i = j
            continue

        if ch == '"':
            # Двойные: экран — обратный апостроф, плюс удвоение. Одинарная
            # кавычка внутри двойных строку не рвёт.
            j = i + 1
            while j < n:
                if text[j] == "`":
                    j += 2
                    continue
                if text[j] == '"':
                    if j + 1 < n and text[j + 1] == '"':
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            blank(i, j)
            i = j
            continue

        out.append(ch)
        i += 1

    return "".join(out)


# Левая часть: `[Console]::OutputEncoding`; `[System.Console]::` тоже годится.
# `InputEncoding` сюда не попадает — это ДРУГОЕ свойство, и оно не про то, чем
# консоль печатает.
_LHS = r"\[\s*(?:System\s*\.\s*)?Console\s*\]\s*::\s*OutputEncoding"

# Правая часть — ТОЛЬКО UTF-8, и это ловушка контракта №1: объявить кодировку
# можно и в cp1251 (`[Text.Encoding]::GetEncoding(1251)`). Признак обязан
# смотреть, ЧТО присвоено, а не только КУДА. Отказ здесь устроен
# НЕСОВПАДЕНИЕМ, а не чёрным списком: всё неперечисленное не проходит само
# собой, и завтрашний способ соврать не проскочит мимо списка, которого нет.
# `System.` перед `Text.` необязателен: PowerShell так разрешает.
_RHS_UTF8 = (
    r"\[\s*(?:System\s*\.\s*)?Text\s*\.\s*UTF8Encoding\s*\]\s*::\s*new\s*\("
    r"|"
    r"\[\s*(?:System\s*\.\s*)?Text\s*\.\s*Encoding\s*\]\s*::\s*UTF8(?![\w])"
)

# Пробелы вокруг `=` и регистр имён свободны — PowerShell регистра не
# различает. `==`, `+=` и `-eq` сюда не попадают: после `=` сразу требуется
# открывающая скобка типа. `$OutputEncoding = ...` тоже мимо: это другое
# направление (чем PowerShell кодирует ВХОД нативной команде), и признаком оно
# не является, хотя ставить его рядом можно.
_DECLARES = re.compile(_LHS + r"\s*=\s*(?:" + _RHS_UTF8 + r")", re.IGNORECASE)

# «Первая печать» — определение ЛИТЕРАЛЬНОЕ (контракт §3.2), ровно эти девять
# имён. Границы с обеих сторон, чтобы `My-Write-Host` и `Write-HostEx` печатью
# не считались. Печати нет вовсе -> правило позиции выполнено: файлу без
# печати ломать нечего.
_PRINTS = re.compile(
    r"(?<![\w-])(?:"
    r"Write-Host|Write-Output|Write-Error|Write-Warning|Write-Verbose"
    r"|Write-Information|Write-Debug|Out-Host|Tee-Object"
    r")(?![\w-])",
    re.IGNORECASE)


def _scan_source(source: str) -> tuple:
    """(код без комментариев, позиция объявления, позиция первой печати)."""
    code = strip_ps_comments(source)
    decl = _DECLARES.search(code)
    printing = _PRINTS.search(code)
    return (code,
            decl.start() if decl is not None else None,
            printing.start() if printing is not None else None)


def _line_of(code: str, pos: int) -> int:
    """Номер строки (с единицы) для позиции в тексте — ради читаемого вердикта."""
    return code.count("\n", 0, pos) + 1


def declares_console_utf8(source: str) -> bool:
    """Объявляет ли УЖЕ ДЕКОДИРОВАННЫЙ текст `.ps1` кодировку консоли UTF-8.

    Истинно, когда выполнено ВСЁ: присваивание `[Console]::OutputEncoding`
    есть, оно не в комментарии и не в строковом литерале, правая часть —
    UTF-8, и стоит оно РАНЬШЕ первой печати.

    Позиция — часть признака, а не придирка: объявление после первых
    `Write-Host` оставляет эти строки уже уехавшими битыми, а в аварии читают
    как раз первые строки.

    Сравниваются позиции в СИМВОЛАХ, а не номера строк: `[Console]::... ;
    Write-Host` на одной строке — это защита, а `Write-Host ; [Console]::...`
    на той же одной строке — нет.
    """
    _, decl, first_print = _scan_source(source)
    if decl is None:
        return False
    return first_print is None or decl < first_print


# ── Разбор записи снимка ────────────────────────────────────────────────────

def _read_record(record: Any) -> tuple:
    """(имя задачи, строка аргументов или None, исполнитель или None).

    Форма записи намеренно НЕ одна: сборщик снимка живёт снаружи и может
    отдавать что угодно разумное. Жёсткая форма здесь означала бы, что «не
    понял запись» выглядит как «задачи нет», а это ровно та склейка, которой
    вся сверка и избегает.

    Исполнителя может не быть вовсе — это законно: старые снимки несли только
    имя и аргументы. Пустая строка считается ОТСУТСТВИЕМ: «поле есть, но
    пустое» ничем не отличается от «поля нет», и притворяться, будто мы знаем
    исполнителя, нельзя. То же и про аргументы.
    """
    def clean(value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    if isinstance(record, Mapping):
        name = record.get("name") or record.get("task") or record.get("TaskName")
        args = clean(record.get("arguments") or record.get("args")
                     or record.get("Arguments"))
        executable = clean(record.get("exec") or record.get("execute")
                           or record.get("Execute"))
        return (str(name) if name is not None else None), args, executable

    name = getattr(record, "name", None) or getattr(record, "task", None)
    if name is not None:
        args = clean(getattr(record, "arguments", None)
                     or getattr(record, "args", None))
        executable = clean(getattr(record, "exec", None)
                           or getattr(record, "execute", None)
                           or getattr(record, "Execute", None))
        return str(name), args, executable

    if isinstance(record, (tuple, list)) and len(record) >= 2:
        executable = clean(record[2]) if len(record) >= 3 else None
        return str(record[0]), clean(record[1]), executable

    return None, None, None


KIND_POWERSHELL = "powershell"
KIND_PYTHON = "python"
KIND_UNKNOWN = "unknown"


def _stem(executable: str) -> str:
    """Имя исполняемого файла без пути, кавычек и `.exe`, в нижнем регистре."""
    tail = executable.strip().strip('"').replace("/", "\\").rsplit("\\", 1)[-1]
    if tail.lower().endswith(".exe"):
        tail = tail[:-4]
    return tail.lower()


def _executor_kind(executable: Optional[str]) -> str:
    """Вид исполнителя по базовому имени `Execute`, регистронезависимо.

    Полный путь венва тоже узнаётся: решает базовое имя, а не путь. Всё
    прочее, пустое и отсутствующее — `unknown`; опознание чужого вида идёт
    только ПОЛОЖИТЕЛЬНОЕ.
    """
    if executable is None:
        return KIND_UNKNOWN
    stem = _stem(executable)
    if stem in ("powershell", "pwsh"):
        return KIND_POWERSHELL
    if stem in ("python", "pythonw"):
        return KIND_PYTHON
    return KIND_UNKNOWN


# ── Сверка ──────────────────────────────────────────────────────────────────

def compare_tasks(snapshot: Iterable[Any],
                  *,
                  expectation: Optional[Mapping] = None,
                  exempt_reasons: Optional[Mapping] = None,
                  read_script: Optional[Callable[[str], bytes]] = None,
                  ) -> list:
    """Сверить СНИМОК живых задач с литеральным ожиданием.

    Возвращает список `TaskVerdict` — по одному на каждое имя, встреченное
    хотя бы с одной стороны. Порядок: сначала ожидаемые (в порядке ожидания),
    затем неожиданные (в порядке снимка), чтобы вывод был устойчив.

    В планировщик НЕ ходит: снимок приходит снаружи. Файлов не открывает:
    `read_script(путь) -> байты` приходит снаружи ТОЖЕ, и любое его исключение
    означает «не прочитали» и наружу НЕ вылетает. Отсутствие читателя — не
    повод промолчать: без него `ps_console`-задачи дают `unreadable`, то есть
    КРАСНОЕ.

    Граница вслух: модуль не отвечает за то, что читатель принесёт именно тот
    файл, который исполняет Планировщик. За соответствие пути отвечают разбор
    строки запуска и сборщик снимка; ошибка там выглядит как `unreadable`, а
    не как зелёное.
    """
    expectation = (TASK_ENCODING_EXPECTATION if expectation is None
                   else expectation)
    exempt_reasons = (EXEMPT_REASONS if exempt_reasons is None
                      else exempt_reasons)

    seen: dict = {}
    counts: dict = {}
    order: list = []
    for record in snapshot or ():
        name, args, executable = _read_record(record)
        if name is None:
            continue
        if name not in counts:
            order.append(name)
            counts[name] = 0
            seen[name] = (args, executable)
        counts[name] += 1

    out: list = []

    for name, protection in expectation.items():
        # ИСКЛЮЧЕНИЕ СИЛЬНЕЕ ОТСУТСТВИЯ, и это решение, а не мелочь.
        #
        # Соблазн обратный: «задача исчезла — скажи об этом, даже если чинить
        # её мы не собирались». Но предмет ЭТОГО сторожа — кодировка. Скажи он
        # `missing` про исключённую, и лампа загорится по причине, к кодировке
        # отношения не имеющей: владельцу придётся либо чинить вне арки, либо
        # гасить сигнал. «Красное при полном порядке» приучает не читать
        # красное — а это ровно то, ради чего сторож и заводился.
        #
        # Граница, которую надо знать вслух: удалённая ИСКЛЮЧЁННАЯ задача этим
        # сторожем не видна. Её существование обязан стеречь тот, кто за неё
        # отвечает, а не сверка кодировки.
        if protection == PROTECTION_EXEMPT:
            why = str(exempt_reasons.get(name) or "").strip()
            if not why:
                # Утверждение «вынесено осознанно» без записанной причины
                # НЕДОКАЗУЕМО. Раньше здесь было зелёное с текстом «причина не
                # записана» — так больше нельзя (контракт §6.6).
                out.append(TaskVerdict(
                    task=name, state=STATE_UNREADABLE,
                    reason=REASON_EXEMPT_WITHOUT_REASON,
                    detail="задача помечена исключённой, но причина нигде не "
                           "записана: «вынесено осознанно» ничем не "
                           "подтверждается и неотличимо от забытой строки"))
                continue
            out.append(TaskVerdict(
                task=name, state=STATE_EXEMPT, reason=REASON_EXEMPT,
                detail=why))
            continue

        # Мусорное значение защиты в ожидании: мы не знаем, ЧТО проверять,
        # значит признак недоказуем. Не падаем исключением и не зеленеем по
        # ветке «наверное, питон»: «недоказуем» != «прошёл».
        if protection not in (PROTECTION_X_UTF8, PROTECTION_PS_CONSOLE):
            out.append(TaskVerdict(
                task=name, state=STATE_UNREADABLE,
                reason=REASON_UNKNOWN_PROTECTION,
                detail="в ожидании стоит неизвестный вид защиты %r: проверять "
                       "нечего, признак недоказуем" % (protection,)))
            continue

        if counts.get(name, 0) > 1:
            # Раньше сверка молча оставляла последнюю запись. Так нельзя:
            # неизвестно, о какой записи вердикт.
            out.append(TaskVerdict(
                task=name, state=STATE_UNREADABLE,
                reason=REASON_DUPLICATE_IN_SNAPSHOT,
                detail="имя встречено в снимке %d раз: о какой из записей "
                       "вердикт — неизвестно" % (counts[name],)))
            continue

        if name not in seen:
            out.append(TaskVerdict(
                task=name, state=STATE_MISSING, reason=REASON_MISSING,
                detail="задача есть в ожидании, но в системе её нет"))
            continue

        args, executable = seen[name]
        kind = _executor_kind(executable)

        if protection == PROTECTION_X_UTF8:
            # АСИММЕТРИЯ, и она осознанная: у `x_utf8` отсутствие или
            # неузнанный исполнитель поведения НЕ меняют — старые снимки
            # `Execute` не несли, и пины первой формы обязаны остаться в силе.
            # У `ps_console` то же отсутствие даёт `unreadable`: там без
            # исполнителя нельзя даже сказать, наш ли это признак. Асимметрия
            # обязана быть припинена ОБОИМИ случаями, иначе через месяц её
            # примут за дырку и «починят», а вместе с ней сломают 17 пинов.
            if kind == KIND_POWERSHELL:
                out.append(TaskVerdict(
                    task=name, state=STATE_KIND_MISMATCH,
                    reason=REASON_KIND_MISMATCH,
                    detail="ожидается `-X utf8` (питоновская задача), а "
                           "задачу запускает %r: признак стал не про неё"
                           % (executable,)))
            elif has_x_utf8(args or ""):
                out.append(TaskVerdict(
                    task=name, state=STATE_OK, reason=REASON_OK,
                    detail="`-X utf8` на месте"))
            else:
                out.append(TaskVerdict(
                    task=name, state=STATE_UNPROTECTED,
                    reason=REASON_UNPROTECTED,
                    detail="в аргументах нет `-X utf8`: диагностика этой "
                           "задачи порвётся кодировкой ровно тогда, когда её "
                           "читают"))
            continue

        out.append(_ps_console_verdict(name, args, executable, kind,
                                       read_script))

    for name in order:
        if name in expectation:
            continue
        if counts[name] > 1:
            out.append(TaskVerdict(
                task=name, state=STATE_UNREADABLE,
                reason=REASON_DUPLICATE_IN_SNAPSHOT,
                detail="имя встречено в снимке %d раз: о какой из записей "
                       "вердикт — неизвестно" % (counts[name],)))
            continue
        out.append(TaskVerdict(
            task=name, state=STATE_UNEXPECTED, reason=REASON_UNEXPECTED,
            detail="задача есть в системе, но её нет в ожидании — "
                   "завели и забыли про кодировку"))

    return out


def _ps_console_verdict(name: str,
                        args: Optional[str],
                        executable: Optional[str],
                        kind: str,
                        read_script: Optional[Callable[[str], bytes]],
                        ) -> TaskVerdict:
    """Вердикт по одной PowerShell-задаче. Порядок проверок СТРОГИЙ.

    Способов НЕ доказать признак шесть, и все шесть красные `unreadable` с
    РАЗНЫМИ причинами: исполнитель не узнан, аргументов нет, читателя нет,
    строка запуска не разобрана, читатель отказал, байты не декодируются.
    Зелёное здесь бывает ровно одно: файл прочитан, декодирован по правилу
    интерпретатора, объявление найдено вне комментариев и раньше первой
    печати.
    """
    def unreadable(reason: str, detail: str) -> TaskVerdict:
        return TaskVerdict(task=name, state=STATE_UNREADABLE,
                           reason=reason, detail=detail)

    if kind == KIND_UNKNOWN:
        # Fail-closed: без узнанного исполнителя нельзя сказать даже, наш ли
        # это признак.
        return unreadable(
            REASON_EXECUTE_UNKNOWN,
            "исполнитель задачи не назван или не узнан (%r): не с чем сверить "
            "вид защиты, признак недоказуем" % (executable,))

    if kind == KIND_PYTHON:
        return TaskVerdict(
            task=name, state=STATE_KIND_MISMATCH, reason=REASON_KIND_MISMATCH,
            detail="ожидается объявленная кодировка консоли в `.ps1`, а "
                   "задачу запускает %r: признак стал не про неё"
                   % (executable,))

    if not args:
        return unreadable(
            REASON_NO_ARGUMENTS,
            "у записи снимка нет строки аргументов: неизвестно, какой файл "
            "исполняется")

    if read_script is None:
        return unreadable(
            REASON_NO_READER,
            "читатель скриптов не передан (`read_script=None`): содержимого "
            "не добыть, а зелёное от нехватки ресурса — не сторож")

    path = script_path_from_arguments(args)
    if path is None:
        return unreadable(
            REASON_ARGUMENTS_UNPARSED,
            "строку запуска не разобрали: явного `-File <путь>` в ней нет "
            "(%r) — неизвестно, какой файл проверять" % (args,))

    try:
        raw = read_script(path)
    except Exception as exc:  # noqa: BLE001
        # Исключение не глотается, а ПЕРЕВОДИТСЯ в красный вердикт с названным
        # типом: это и есть его обработка — отчёт уходит наружу тем же списком.
        return unreadable(
            REASON_READER_FAILED,
            "читатель отказал на %s: %s (%s)"
            % (path, type(exc).__name__, exc))

    try:
        source = decode_ps1_bytes(raw)
    except Exception as exc:  # noqa: BLE001
        return unreadable(
            REASON_DECODE_FAILED,
            "байты %s не декодируются по правилу PowerShell 5.1 (utf-8 при "
            "BOM, cp1251 без него): %s (%s)"
            % (path, type(exc).__name__, exc))

    code, decl, first_print = _scan_source(source)

    if decl is None:
        # Файл прочитан и разобран, сомнений в факте нет: признака просто нет.
        # Это `unprotected`, а не `unreadable`, — чинится правкой файла.
        return TaskVerdict(
            task=name, state=STATE_UNPROTECTED, reason=REASON_UNPROTECTED,
            detail="в %s нет присваивания `[Console]::OutputEncoding` "
                   "значением UTF-8 вне комментариев и строк: вывод этой "
                   "задачи порвётся кодировкой ровно тогда, когда его читают"
                   % (path,))

    if first_print is not None and decl > first_print:
        return TaskVerdict(
            task=name, state=STATE_UNPROTECTED, reason=REASON_DECLARED_LATE,
            detail="объявление кодировки в %s стоит в строке %d, а первая "
                   "печать — уже в строке %d: эти строки уезжают битыми. "
                   "Править ПЕРЕСТАНОВКОЙ, а не дописыванием"
                   % (path, _line_of(code, decl), _line_of(code, first_print)))

    return TaskVerdict(
        task=name, state=STATE_OK, reason=REASON_OK,
        detail="%s объявляет `[Console]::OutputEncoding` в UTF-8 (строка %d), "
               "раньше первой печати" % (path, _line_of(code, decl)))
