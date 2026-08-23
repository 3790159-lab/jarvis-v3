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

ВТОРАЯ ФОРМА (DEV-58b). Половина парка запускается не питоном, а PowerShell, и
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

TASK_ENCODING_EXPECTATION: dict = {
    "JarvisStateBackup": PROTECTION_X_UTF8,
    "JarvisRestoreDrill": PROTECTION_X_UTF8,
    "JarvisChatterCacheDigest": PROTECTION_X_UTF8,
    "JarvisDrillNightly": PROTECTION_X_UTF8,
    "JarvisIgTokenRefresh": PROTECTION_EXEMPT,
    "JarvisBackendGuardian": PROTECTION_PS_CONSOLE,
    "JarvisBotGuardian": PROTECTION_PS_CONSOLE,
    "JarvisChatterGuardian": PROTECTION_PS_CONSOLE,
    "JarvisOpsWatchdog": PROTECTION_PS_CONSOLE,
    "JarvisPanelClientGuardian": PROTECTION_PS_CONSOLE,
    "JarvisHealthchecksPing": PROTECTION_PS_CONSOLE,
    "JarvisInfraRestartCloudflared": PROTECTION_PS_CONSOLE,
    "JarvisSniperDetached": PROTECTION_EXEMPT,
}

# ОЖИДАЕМОЕ КРАСНОЕ, названное заранее: на живой машине семь `ps_console`
# задач сегодня дадут `unprotected` — `[Console]::OutputEncoding` в этих
# файлах ещё нет. Сторож называет работу, которая идёт следующим шагом и в
# строгом порядке (объявить -> рестарт -> приёмка), а не докладывает о беде.

# Причина исключения — МАШИНОЧИТАЕМАЯ и отдельным словарём.
#
# Отдельным, а не полем в таблице выше: таблица обязана остаться простым
# отображением «имя -> защита», иначе её нельзя прочитать одним взглядом. А
# исключение БЕЗ причины через месяц неотличимо от потерянной строки — и тогда
# следующий читатель не узнает, забыли её или вынесли осознанно.
EXEMPT_REASONS: dict = {
    "JarvisIgTokenRefresh":
        "решение владельца 24.08: вне арки DEV-58, разбирается отдельно",
    "JarvisSniperDetached":
        "задача отставлена намеренно: триггеров нет, денег не тратит",
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


@dataclass(frozen=True)
class TaskVerdict:
    """Вердикт по одной задаче.

    `reason` присутствует ВСЕГДА, в том числе на зелёном, — как у проб в
    `ops_watchdog`. Иначе состояния неразличимы машинно и дедуп по причине не
    увидит смены красного на другое красное.

    Граница вслух: `reason` — машиночитаемый КЛЮЧ состояния и ничего больше;
    человеческий текст (тип исключения читателя, путь, номера строк) живёт в
    `detail`. Пять способов не доказать признак идут под одним ключом
    `unreadable` намеренно: лампа одна, потому что и действие одно — починить
    шов, а не файл.
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
    пути необязательны и снимаются. `-Command`, `-EncodedCommand` и просто
    отсутствие `-File` дают None — и задача получает `unreadable`, а не
    зелёное: мы не знаем, какой файл проверять, и признать это вслух дешевле,
    чем проверить не тот.

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


def _decode_ps1(raw: bytes) -> Optional[str]:
    """Байты `.ps1` -> текст ПО ПРАВИЛУ PowerShell 5.1, иначе None.

    BOM есть -> utf-8; BOM нет -> cp1251. Правило не наше, а интерпретатора:
    читай сторож всегда как utf-8, он проверял бы не тот текст, который
    исполняется. Из восьми живых `.ps1` BOM есть у пяти, нет у трёх — ветка
    без BOM не гипотетическая, она живая.

    Байты, не декодируемые ни так ни так (в cp1251 не определён 0x98), дают
    None: это `unreadable`, а не `unprotected`.
    """
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        return None
    data = bytes(raw)
    try:
        if data.startswith(_UTF8_BOM):
            return data[len(_UTF8_BOM):].decode("utf-8")
        return data.decode("cp1251")
    except (UnicodeDecodeError, LookupError):
        return None


def _blank_comments_and_strings(text: str) -> str:
    """Погасить комментарии и строковые литералы, СОХРАНИВ позиции символов.

    Намеренная слепота — тот же класс, что дал нам AST-сторож одобренного
    текста: присваивание, найденное в комментарии или в кавычках, защитой не
    является, а `#` внутри кавычек комментария не открывает.

    Погашенное заменяется пробелами, переводы строк остаются на местах: длина
    и разбивка на строки обязаны совпасть с исходником, иначе и «раньше первой
    печати», и номера строк в вердикте соврут.

    Граница вслух: here-strings (@'...'@ и @"..."@) отдельно НЕ разбираются —
    контракт §2.2 их не называет, а в живых восьми файлах объявление кодировки
    внутри here-string не встречается. Худшее, что даёт эта слепота, — ложное
    «не защищено», то есть красное, а не зелёное.
    """
    out: list = []
    i, n = 0, len(text)

    def blank(start: int, end: int) -> None:
        for ch in text[start:end]:
            out.append("\n" if ch == "\n" else " ")

    while i < n:
        ch = text[i]

        if text.startswith("<#", i):  # блочный комментарий, часто многострочный
            end = text.find("#>", i + 2)
            end = n if end < 0 else end + 2
            blank(i, end)
            i = end
            continue

        if ch == "#":  # строчный комментарий до конца строки
            end = text.find("\n", i)
            end = n if end < 0 else end
            blank(i, end)
            i = end
            continue

        if ch == "'":  # одинарные кавычки: единственный экран — удвоение
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

        if ch == '"':  # двойные: экран — обратный апостроф, плюс удвоение
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
_LHS = r"\[\s*(?:System\s*\.\s*)?Console\s*\]\s*::\s*OutputEncoding"

# Правая часть — ТОЛЬКО UTF-8, и это ловушка контракта №2: объявить кодировку
# можно и в cp1251 (`[Text.Encoding]::GetEncoding(1251)`). Признак обязан
# смотреть, ЧТО присвоено, а не только КУДА. Отказ здесь устроен
# НЕСОВПАДЕНИЕМ, а не чёрным списком: всё неперечисленное не проходит само
# собой, и завтрашний способ соврать не проскочит мимо списка, которого нет.
_RHS_UTF8 = (
    r"\[\s*(?:System\s*\.\s*)?Text\s*\.\s*UTF8Encoding\s*\]\s*::\s*new\s*\("
    r"|"
    r"\[\s*(?:System\s*\.\s*)?Text\s*\.\s*Encoding\s*\]\s*::\s*UTF8(?![\w])"
)

# Пробелы вокруг `=` и регистр имён свободны — PowerShell регистра не
# различает. `==`, `+=` и `-eq` сюда не попадают: после `=` сразу требуется
# открывающая скобка типа.
_DECLARES = re.compile(_LHS + r"\s*=\s*(?:" + _RHS_UTF8 + r")", re.IGNORECASE)

# «Печать» по контракту §2.3 — ровно эти пять командлетов. Границы с обеих
# сторон, чтобы `My-Write-Host` и `Write-HostEx` печатью не считались.
_PRINTS = re.compile(
    r"(?<![\w-])Write-(?:Host|Output|Error|Warning|Information)(?![\w-])",
    re.IGNORECASE)


def _scan_console_encoding(raw: bytes):
    """(код без комментариев, позиция объявления, позиция первой печати).

    None вместо кортежа — «байты не декодировались». Разводить эти два исхода
    обязан вызывающий: `declares_console_encoding` отвечает булевым, а вердикт
    в `compare_tasks` — разными состояниями.
    """
    text = _decode_ps1(raw)
    if text is None:
        return None
    code = _blank_comments_and_strings(text)
    decl = _DECLARES.search(code)
    printing = _PRINTS.search(code)
    return (code,
            decl.start() if decl is not None else None,
            printing.start() if printing is not None else None)


def _line_of(code: str, pos: int) -> int:
    """Номер строки (с единицы) для позиции в тексте — ради читаемого вердикта."""
    return code.count("\n", 0, pos) + 1


def declares_console_encoding(raw: bytes) -> bool:
    """Объявляет ли `.ps1` кодировку консоли UTF-8 РАНЬШЕ первой печати.

    Позиция — часть признака, а не придирка: объявление после первых
    `Write-Host` оставляет эти строки уже уехавшими битыми, а в аварии читают
    как раз первые строки.

    Сравниваются позиции в СИМВОЛАХ, а не номера строк: `[Console]::... ;
    Write-Host` на одной строке — это защита, а `Write-Host ; [Console]::...`
    на той же одной строке — нет.

    Нечитаемые байты дают False: булев ответ не умеет сказать «не доказано»,
    поэтому недоказуемость разводит `compare_tasks` отдельным состоянием.
    """
    scan = _scan_console_encoding(raw)
    if scan is None:
        return False
    _, decl, first_print = scan
    if decl is None:
        return False
    return first_print is None or decl < first_print


# ── Разбор записи снимка ────────────────────────────────────────────────────

def _read_record(record: Any) -> tuple:
    """(имя задачи, строка аргументов, исполнитель или None) из записи снимка.

    Форма записи намеренно НЕ одна: сборщик снимка живёт снаружи и может
    отдавать что угодно разумное. Жёсткая форма здесь означала бы, что «не
    понял запись» выглядит как «задачи нет», а это ровно та склейка, которой
    вся сверка и избегает.

    Исполнителя может не быть вовсе — это законно: старые снимки несли только
    имя и аргументы. Пустая строка считается ОТСУТСТВИЕМ: «поле есть, но
    пустое» ничем не отличается от «поля нет», и притворяться, будто мы знаем
    исполнителя, нельзя.
    """
    def clean(value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    if isinstance(record, Mapping):
        name = record.get("name") or record.get("task") or record.get("TaskName")
        args = (record.get("arguments") or record.get("args")
                or record.get("Arguments") or "")
        executable = clean(record.get("exec") or record.get("execute")
                           or record.get("Execute"))
        return (str(name) if name is not None else None), str(args), executable

    name = getattr(record, "name", None) or getattr(record, "task", None)
    if name is not None:
        args = (getattr(record, "arguments", None)
                or getattr(record, "args", None) or "")
        executable = clean(getattr(record, "exec", None)
                           or getattr(record, "execute", None)
                           or getattr(record, "Execute", None))
        return str(name), str(args), executable

    if isinstance(record, (tuple, list)) and len(record) >= 2:
        executable = clean(record[2]) if len(record) >= 3 else None
        return str(record[0]), str(record[1]), executable

    return None, "", None


def _stem(executable: str) -> str:
    """Имя исполняемого файла без пути, кавычек и `.exe`, в нижнем регистре."""
    tail = executable.strip().strip('"').replace("/", "\\").rsplit("\\", 1)[-1]
    if tail.lower().endswith(".exe"):
        tail = tail[:-4]
    return tail.lower()


def _is_powershell(executable: str) -> bool:
    return _stem(executable) in ("powershell", "pwsh")


def _is_python(executable: str) -> bool:
    # `python`, `pythonw`, `python3`, `python3.11` — всё это питон; другого
    # семейства с таким началом в парке нет.
    return _stem(executable).startswith("python")


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
    означает «не прочитали». Отсутствие читателя — не повод промолчать: без
    него `ps_console`-задачи дают `unreadable`, то есть КРАСНОЕ.

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
    order: list = []
    for record in snapshot or ():
        name, args, executable = _read_record(record)
        if name is None:
            continue
        if name not in seen:
            order.append(name)
        seen[name] = (args, executable)

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
            why = exempt_reasons.get(name, "причина исключения не записана")
            out.append(TaskVerdict(
                task=name, state=STATE_EXEMPT, reason=STATE_EXEMPT,
                detail=why))
            continue

        # Мусорное значение защиты в ожидании. Контракт про него молчит, и
        # молчание — не разрешение промолчать: мы не знаем, ЧТО проверять,
        # значит признак недоказуем. `unreadable`, а не тихое зелёное по ветке
        # «наверное, питон»: «недоказуем» != «прошёл».
        if protection not in (PROTECTION_X_UTF8, PROTECTION_PS_CONSOLE):
            out.append(TaskVerdict(
                task=name, state=STATE_UNREADABLE, reason=STATE_UNREADABLE,
                detail="в ожидании стоит неизвестный вид защиты %r: проверять "
                       "нечего, признак недоказуем" % (protection,)))
            continue

        if name not in seen:
            out.append(TaskVerdict(
                task=name, state=STATE_MISSING,
                reason=STATE_MISSING,
                detail="задача есть в ожидании, но в системе её нет"))
            continue

        args, executable = seen[name]

        # Вид защиты сверяется с исполнителем. Задачу могли перенаправить на
        # другой исполнитель — тогда признак, который мы проверяем, стал не про
        # неё вовсе, и чинить надо не строку запуска, а разбираться, кто и
        # зачем переписал действие.
        if protection == PROTECTION_X_UTF8:
            kind, matches = "python", _is_python
        else:
            kind, matches = "powershell", _is_powershell

        if executable is not None and not matches(executable):
            out.append(TaskVerdict(
                task=name, state=STATE_KIND_MISMATCH,
                reason=STATE_KIND_MISMATCH,
                detail="ожидается защита %s (исполнитель %s), а задачу "
                       "запускает %r" % (protection, kind, executable)))
            continue

        if protection == PROTECTION_X_UTF8:
            # АСИММЕТРИЯ, и она осознанная: у `x_utf8` отсутствие исполнителя
            # поведения НЕ меняет — старые снимки его не несли, и пины первой
            # формы обязаны остаться в силе. У `ps_console` то же отсутствие
            # даёт `unreadable`: там без исполнителя нельзя даже сказать, наш
            # ли это признак. Асимметрия обязана быть припинена ОБОИМИ
            # случаями, иначе через месяц её примут за дырку и «починят».
            if has_x_utf8(args):
                out.append(TaskVerdict(
                    task=name, state=STATE_OK, reason=STATE_OK,
                    detail="`-X utf8` на месте"))
            else:
                out.append(TaskVerdict(
                    task=name, state=STATE_UNPROTECTED,
                    reason=STATE_UNPROTECTED,
                    detail="в аргументах нет `-X utf8`: диагностика этой "
                           "задачи порвётся кодировкой ровно тогда, когда её "
                           "читают"))
            continue

        out.append(_ps_console_verdict(name, args, executable, read_script))

    for name in order:
        if name in expectation:
            continue
        out.append(TaskVerdict(
            task=name, state=STATE_UNEXPECTED, reason=STATE_UNEXPECTED,
            detail="задача есть в системе, но её нет в ожидании — "
                   "завели и забыли про кодировку"))

    return out


def _ps_console_verdict(name: str,
                        args: str,
                        executable: Optional[str],
                        read_script: Optional[Callable[[str], bytes]],
                        ) -> TaskVerdict:
    """Вердикт по одной PowerShell-задаче.

    Способов НЕ доказать признак пять, и все пять красные `unreadable`:
    исполнитель не назван, строка запуска не разобрана, читателя нет, читатель
    отказал, байты не декодируются. Зелёное здесь бывает ровно одно: файл
    прочитан, декодирован по правилу интерпретатора, объявление найдено вне
    комментариев и раньше первой печати.
    """
    def unreadable(detail: str) -> TaskVerdict:
        return TaskVerdict(task=name, state=STATE_UNREADABLE,
                           reason=STATE_UNREADABLE, detail=detail)

    if executable is None:
        return unreadable(
            "исполнитель в записи снимка не назван: не с чем сверить вид "
            "защиты, признак недоказуем")

    path = script_path_from_arguments(args)
    if path is None:
        return unreadable(
            "строку запуска не разобрали: явного `-File <путь>` в ней нет "
            "(%r) — неизвестно, какой файл проверять" % (args,))

    if read_script is None:
        return unreadable(
            "читатель скриптов не передан (`read_script=None`): содержимое %s "
            "не добыть, а зелёное от нехватки ресурса — не сторож" % (path,))

    try:
        raw = read_script(path)
    except Exception as exc:  # noqa: BLE001
        # Исключение не глотается, а ПЕРЕВОДИТСЯ в красный вердикт с названным
        # типом: это и есть его обработка — отчёт уходит наружу тем же списком.
        return unreadable(
            "читатель отказал на %s: %s (%s)" % (path, type(exc).__name__, exc))

    scan = _scan_console_encoding(raw)
    if scan is None:
        return unreadable(
            "байты %s не декодируются ни как utf-8 (при BOM), ни как cp1251 "
            "(без BOM) — проверять нечего" % (path,))

    code, decl, first_print = scan

    if decl is None:
        # Файл прочитан и разобран, сомнений в факте нет: признака просто нет.
        # Это `unprotected`, а не `unreadable`, — чинится правкой файла.
        return TaskVerdict(
            task=name, state=STATE_UNPROTECTED, reason=STATE_UNPROTECTED,
            detail="в %s нет присваивания `[Console]::OutputEncoding` "
                   "значением UTF-8 вне комментариев и строк: вывод этой "
                   "задачи порвётся кодировкой ровно тогда, когда его читают"
                   % (path,))

    if first_print is not None and decl > first_print:
        return TaskVerdict(
            task=name, state=STATE_UNPROTECTED, reason=STATE_UNPROTECTED,
            detail="объявление кодировки в %s стоит в строке %d, а первая "
                   "печать — уже в строке %d: эти строки уезжают битыми"
                   % (path, _line_of(code, decl), _line_of(code, first_print)))

    return TaskVerdict(
        task=name, state=STATE_OK, reason=STATE_OK,
        detail="%s объявляет `[Console]::OutputEncoding` в UTF-8 (строка %d), "
               "раньше первой печати" % (path, _line_of(code, decl)))
