"""DEV-58: мутационный гейт на сверку защиты кодировки у задач.

Предмет — список, который обязан краснеть на задаче, заведённой завтра и
забытой. Сторож, не краснеющий на снятой обратной сверке, превращает ожидание
в описание сегодняшнего дня: оно согласно с системой по определению и молчит
ровно там, где нужно.

Прогон: python scripts/mutate_task_encoding.py (в worktree, дерево чистое)
"""
from __future__ import annotations

import os
import subprocess
import sys
from itertools import count
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gate_guard import refuse_if_live_tree   # DEV-31: гейт мутирует только worktree

ROOT = Path(__file__).resolve().parents[1]

_MTIME_BASE = 2_000_000_000
_mtime_seq = count()

M = "app/services/task_encoding.py"
R = "scripts/task_encoding_report.py"
T = "tests/test_scheduled_task_encoding.py"

# DEV-59. Указывается ФАЙЛ сторожей целиком, а не отдельный тест: мутации пишет
# автор кода, сторожей он не видел и видеть не должен — состязательность в этом
# и состоит. Имя отдельного теста пришлось бы подсмотреть, а файл известен из
# контракта.
T_SIGNAL = "tests/test_dev59_ps_console_signal.py"
T_PARSE = "tests/test_dev59_decode_and_arguments.py"
T_STATES = "tests/test_dev59_compare_tasks_states.py"
T_REPORT = "tests/test_dev59_report_pure.py"

MUTATIONS = [
    ("имя выпало из таблицы ожидания — забытая задача перестала ожидаться", M,
     [('    "JarvisDrillNightly": PROTECTION_X_UTF8,\n', "")],
     T_STATES),

    ("исключение превращено в обязательство — решение владельца стёрто", M,
     [('    "JarvisIgTokenRefresh": PROTECTION_EXEMPT,',
       '    "JarvisIgTokenRefresh": PROTECTION_X_UTF8,')],
     T_STATES),

    ("незащищённая задача объявлена защищённой — весь предмет снят", M,
     [("                    task=name, state=STATE_UNPROTECTED,",
       "                    task=name, state=STATE_OK,"),
      ("                    reason=REASON_UNPROTECTED,",
       "                    reason=REASON_OK,")],
     f"{T}::test_task_without_x_utf8_is_unprotected_not_ok_not_missing"),

    ("защищённая объявлена незащищённой — сторож станет фоном", M,
     [("                    task=name, state=STATE_OK, reason=REASON_OK,",
       "                    task=name, state=STATE_UNPROTECTED, reason=REASON_UNPROTECTED,")],
     f"{T}::test_protected_task_is_ok"),

    ("исключённая задача объявлена нарушением — красное при полном порядке", M,
     [("                task=name, state=STATE_EXEMPT, reason=REASON_EXEMPT,",
       "                task=name, state=STATE_UNPROTECTED, reason=REASON_UNPROTECTED,")],
     f"{T}::test_exempt_task_without_x_utf8_is_exempt_and_not_a_violation"),

    ("пропажа задачи склеена с неожиданной — «что делать» потеряно", M,
     [("                task=name, state=STATE_MISSING, reason=REASON_MISSING,",
       "                task=name, state=STATE_UNEXPECTED, reason=REASON_UNEXPECTED,")],
     f"{T}::test_missing_and_unexpected_are_different_verdicts"),

    ("обратная сверка снята — завтрашняя забытая задача не всплывёт", M,
     [("    for name in order:\n"
       "        if name in expectation:\n"
       "            continue\n",
       "    for name in []:\n"
       "        if name in expectation:\n"
       "            continue\n")],
     f"{T}::test_snapshot_task_absent_from_expectation_is_unexpected"),

    ("правая граница снята — `-X utf8x` сойдёт за защиту", M,
     [(r'_X_UTF8 = re.compile(r"(?<![\w-])-X\s+utf8(?![\w])")',
       r'_X_UTF8 = re.compile(r"(?<![\w-])-X\s+utf8")')],
     f"{T}::test_x_utf8_recognition_is_robust_to_formatting"),

    ("левая граница снята — `--X utf8` сойдёт за защиту", M,
     [(r'_X_UTF8 = re.compile(r"(?<![\w-])-X\s+utf8(?![\w])")',
       r'_X_UTF8 = re.compile(r"-X\s+utf8(?![\w])")')],
     f"{T}::test_x_utf8_recognition_is_robust_to_formatting"),

    ("пробел стал ровно одним — оформление принято за смысл", M,
     [(r'_X_UTF8 = re.compile(r"(?<![\w-])-X\s+utf8(?![\w])")',
       r'_X_UTF8 = re.compile(r"(?<![\w-])-X utf8(?![\w])")')],
     f"{T}::test_x_utf8_recognition_is_robust_to_formatting"),

    ("кавычки не вырезаются — подстрока в ПУТИ сойдёт за защиту", M,
     [('    return bool(_X_UTF8.search(_QUOTED.sub(" ", arguments or "")))',
       '    return bool(_X_UTF8.search(arguments or ""))')],
     f"{T}::test_x_utf8_recognition_is_robust_to_formatting"),

    ("сверка полезла в планировщик сама — сторож стал стендом", M,
     [("import re\nfrom dataclasses import dataclass",
       "import re\nimport subprocess\nfrom dataclasses import dataclass")],
     f"{T}::test_comparison_never_reaches_the_scheduler"),

    ("ожидание перестало подставляться — таблица больше ни на что не влияет", M,
     [("    expectation = (TASK_ENCODING_EXPECTATION if expectation is None\n"
       "                   else expectation)",
       "    expectation = TASK_ENCODING_EXPECTATION")],
     f"{T}::test_injected_expectation_is_used_instead_of_the_module_table"),

    ("внедрённые ПРИЧИНЫ игнорируются — шов есть, а работы в нём нет", M,
     [("    exempt_reasons = (EXEMPT_REASONS if exempt_reasons is None\n"
       "                      else exempt_reasons)",
       "    exempt_reasons = EXEMPT_REASONS")],
     f"{T}::test_injected_exempt_reason_reaches_the_result"),

    ("отсутствие снова сильнее исключения — лампа краснеет при порядке", M,
     [("        if protection == PROTECTION_EXEMPT:",
       "        if protection == PROTECTION_EXEMPT and name in seen:")],
     f"{T}::test_expectation_defaults_to_the_module_table_when_not_injected"),

    # ── DEV-59, ПРИЗНАК: правая часть, слепота, правило позиции ─────────────
    #
    # Все мишени ниже ОДНОСТРОЧНЫЕ и без обратных слэшей: многострочная мишень
    # уже съедалась экранированием по дороге через оболочку. Где мишень обязана
    # быть длиннее строки, берутся ДВЕ правки, а не перенос.
    #
    # И все они ОДНОЗНАЧНЫ: `replace(old, new, 1)` берёт ПЕРВОЕ вхождение, а в
    # коде теперь по два `STATE_OK` и по два `STATE_UNPROTECTED`, различимых
    # только отступом. Мишень, совпавшая не там, мутирует не то место — и это
    # выглядит зелёным. Поэтому, например, `65001` целится подстрокой
    # `(?:65001|`: голое `65001` первым вхождением попало бы в КОММЕНТАРИЙ.

    ("cp1251 объявлена кодировкой UTF-8 — признак перестал смотреть, ЧТО "
     "присвоено", M,
     [("(?:65001|", "(?:1251|65001|")],
     T_SIGNAL),

    ("верное объявление через GetEncoding(65001) отвергнуто — ложный красный "
     "приучает не читать красное", M,
     [("(?:65001|", "(?:65002|")],
     T_SIGNAL),

    ("слепота к СТРОЧНОМУ комментарию снята — объявление в `#` сойдёт за "
     "защиту", M,
     [('        if ch == "#":', "        if False:")],
     T_SIGNAL),

    ("слепота к БЛОЧНОМУ комментарию снята — объявление в `<# #>` сойдёт за "
     "защиту", M,
     [('        if text.startswith("<#", i):', "        if False:")],
     T_SIGNAL),

    ("слепота к СТРОКОВОМУ литералу снята — объявление, напечатанное в "
     "сообщении, сойдёт за защиту", M,
     [("        if blank_strings:", "        if False:")],
     T_SIGNAL),

    ("шов А.1 против §3.3 разомкнут — совпадение внутри литерала больше не "
     "отсеивается позицией", M,
     [("        if code[start] == code_with_strings[start]:",
       "        if True:")],
     T_SIGNAL),

    ("правило позиции снято — объявление ПОСЛЕ первой печати сойдёт за "
     "защиту, а первые строки уедут битыми", M,
     [("    return first_print is None or decl < first_print", "    return True")],
     T_SIGNAL),

    ("список печати урезан — `Out-Host` и `Tee-Object` перестали быть "
     "печатью, и правило позиции ослепло на две команды", M,
     [(r'    r"|Write-Information|Write-Debug|Out-Host|Tee-Object"',
       r'    r"|Write-Information|Write-Debug"')],
     T_SIGNAL),

    # ── DEV-59, ДЕКОДИРОВАНИЕ И РАЗБОР СТРОКИ ЗАПУСКА ───────────────────────

    ("файл с BOM читается как cp1251 — сторож проверяет НЕ ТОТ текст, который "
     "исполняется", M,
     [('        return data[len(_UTF8_BOM):].decode("utf-8")',
       '        return data[len(_UTF8_BOM):].decode("cp1251")')],
     T_PARSE),

    ("файл БЕЗ BOM читается как utf-8 — правило интерпретатора подменено "
     "нашим удобством", M,
     [('    return data.decode("cp1251")', '    return data.decode("utf-8")')],
     T_PARSE),

    ("хвост после `-File` уехал в путь — `-Slug yarina` стал частью имени "
     "файла", M,
     [("            return tokens[idx + 1][0] or None",
       '            return " ".join(t for t, _ in tokens[idx + 1:]) or None')],
     T_PARSE),

    ("кавычки перестали держать путь целиком — `-File` ВНУТРИ пути снова "
     "притворяется флагом", M,
     [("        if ch.isspace() and not in_quotes:", "        if ch.isspace():")],
     T_PARSE),

    # ── DEV-59, СОСТОЯНИЯ, ПРИЧИНЫ, ПОРЯДОК ────────────────────────────────

    ("НЕДОКАЗУЕМОЕ объявлено зелёным — все шесть швов разом: сторож, зелёный "
     "от нехватки ресурса, не сторож", M,
     [("        return TaskVerdict(task=name, state=STATE_UNREADABLE,",
       "        return TaskVerdict(task=name, state=STATE_OK,")],
     T_STATES),

    ("fail-closed на отсутствие читателя вывернут — `read_script=None` "
     "перестал давать `no_reader`", M,
     [("    if read_script is None:", "    if read_script is not None:")],
     T_STATES),

    ("отказ читателя склеен с его отсутствием — дедуп по причине не увидит "
     "смены одного красного на другое", M,
     [("            REASON_READER_FAILED,", "            REASON_NO_READER,")],
     T_STATES),

    ("запись без строки аргументов перестала давать `no_arguments`", M,
     [("    if not args:", "    if False:")],
     T_STATES),

    ("fail-closed на неузнанного исполнителя снят — `ps_console` без "
     "`Execute` пойдёт проверяться вслепую", M,
     [("    if kind == KIND_UNKNOWN:", "    if False:")],
     T_STATES),

    ("`kind_mismatch` снят в сторону ps_console+python — задачу перенаправили "
     "на питон, а сторож этого не заметил", M,
     [("    if kind == KIND_PYTHON:", "    if False:")],
     T_STATES),

    ("`kind_mismatch` снят в сторону x_utf8+powershell — признак стал не про "
     "эту задачу, и это молчит", M,
     [("            if kind == KIND_POWERSHELL:", "            if False:")],
     T_STATES),

    ("АСИММЕТРИЯ снята — снимок без `Execute` у python-задачи стал "
     "`kind_mismatch`, и совместимость со старыми записями рухнула", M,
     [("            if kind == KIND_POWERSHELL:",
       "            if kind != KIND_PYTHON:")],
     T_STATES),

    ("двойное имя в снимке снова молчит — неизвестно, о какой записи вердикт", M,
     [("        if counts.get(name, 0) > 1:", "        if False:")],
     T_STATES),

    ("двойное имя молчит у НЕОЖИДАННОЙ задачи — §6.4 требует обе стороны", M,
     [("        if counts[name] > 1:", "        if False:")],
     T_STATES),

    ("`exempt` без записанной причины снова зелёный — «вынесено осознанно» "
     "опять ничем не подтверждается", M,
     [("            if not why:", "            if False:")],
     T_STATES),

    ("мусорное значение защиты перестало быть красным — проверять нечего, а "
     "лампа не горит", M,
     [("        if protection not in (PROTECTION_X_UTF8, PROTECTION_PS_CONSOLE):",
       "        if False:")],
     T_STATES),

    ("`declared_late` склеен с `unprotected` по причине — «дописать» и "
     "«переставить» стали неразличимы машинно", M,
     [("            task=name, state=STATE_UNPROTECTED, reason=REASON_DECLARED_LATE,",
       "            task=name, state=STATE_UNPROTECTED, reason=REASON_UNPROTECTED,")],
     T_STATES),

    ("объявление ПОСЛЕ печати объявлено зелёным в самой сверке", M,
     [("    if first_print is not None and decl > first_print:",
       "    if False:")],
     T_STATES),

    ("имя выпало из таблицы 13 и появилось четырнадцатое — обратная сверка "
     "обязана поймать ОБЕ стороны", M,
     [('    "JarvisSniperDetached": PROTECTION_PS_CONSOLE,',
       '    "JarvisSniperDetachedX": PROTECTION_PS_CONSOLE,')],
     T_STATES),

    ("вид защиты подменён у живой PowerShell-задачи — её станут проверять "
     "признаком, который про неё ничего не говорит", M,
     [('    "JarvisOpsWatchdog": PROTECTION_PS_CONSOLE,',
       '    "JarvisOpsWatchdog": PROTECTION_X_UTF8,')],
     T_STATES),

    ("ключ исполнителя вернулся к отменённому написанию — живой снимок "
     "перестал быть понятым", M,
     [('        executable = clean(record.get("Execute") or record.get("execute")',
       '        executable = clean(record.get("exec") or record.get("execute")')],
     T_STATES),

    ("питон с версией в имени перестал узнаваться — непризнанный питон у "
     "`x_utf8`-задачи МОЛЧА выключает сверку вида", M,
     [("[0-9._]*w?", "")],
     T_STATES),

    # ── DEV-59, ОТЧЁТНЫЙ СКРИПТ: чистые функции ────────────────────────────

    ("пустой список вердиктов объявлен зелёным — сверка, не увидевшая ни "
     "одной задачи, не «прошла», а не состоялась", R,
     [("    if not verdicts:", "    if False:")],
     T_REPORT),

    ("`unprotected` объявлен зелёным состоянием — код возврата перестал "
     "означать то, ради чего он есть", R,
     [("GREEN_STATES = (STATE_OK, STATE_EXEMPT)",
       "GREEN_STATES = (STATE_OK, STATE_EXEMPT, STATE_UNPROTECTED)")],
     T_REPORT),

    ("причина пропала из строки отчёта — состояния снова неразличимы там, где "
     "действия разные", R,
     [('            getattr(verdict, "reason", ""))', '            "")')],
     T_REPORT),
]


def write_mutant(path: Path, text: str) -> None:
    # .py: BOM ЗАПРЕЩЁН (ast.parse краснеет), переводы строк обязаны остаться
    # LF — write_text на Windows молча сделал бы CRLF и погасил бы мутацию.
    path.write_bytes(text.encode("utf-8"))
    stamp = _MTIME_BASE + next(_mtime_seq)
    os.utime(path, (stamp, stamp))


def run(test: str) -> tuple[bool, str]:
    """КРАСНОЕ — РОВНО rc 1 плюс `failed`: rc 2 это «сбор упал», rc 4 —
    «нет такого теста», и засчитывать их значит поверить в сторожа, которого
    не запускали."""
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    p = subprocess.run(
        [sys.executable, "-m", "pytest", test, "-q", "--no-header",
         "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env)
    out = (p.stdout or "") + (p.stderr or "")
    tail = out.strip().splitlines()[-1] if out.strip() else "(пусто)"
    return (p.returncode == 1 and "failed" in out,
            "rc=%d | %s" % (p.returncode, tail[:140]))


def revert(rel: str) -> None:
    subprocess.run(["git", "checkout", "--", rel], cwd=ROOT, check=True)


def assert_clean() -> None:
    out = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                         cwd=ROOT, capture_output=True, text=True).stdout.strip()
    if out:
        raise SystemExit(
            "ОТКАЗ: рабочее дерево грязное — откат мутаций сотрёт эти правки.\n"
            + out)


def assert_target_matches_code() -> None:
    """Мишени обязаны существовать В КОДЕ до всякой мутации.

    Разъехавшаяся мишень означает «мутация не применилась», а это выглядит как
    зелёное. Сверка стоит секунды и ловит тот класс, где код уехал, а гейт
    остался.
    """
    missing = []
    for name, rel, edits, _test in MUTATIONS:
        text = (ROOT / rel).read_text(encoding="utf-8")
        for old, _new in edits:
            if old not in text:
                missing.append(name)
    if missing:
        raise SystemExit("ОТКАЗ: мишени разъехались с кодом:\n  "
                         + "\n  ".join(missing))


def main() -> int:
    refuse_if_live_tree(ROOT)
    assert_clean()
    assert_target_matches_code()
    blind = []
    for name, rel, edits, test in MUTATIONS:
        path = ROOT / rel
        text = path.read_text(encoding="utf-8")
        mutated = text
        for old, new in edits:
            mutated = mutated.replace(old, new, 1)
        if mutated == text:
            print(f"[!] МУТАЦИЯ НЕ ИЗМЕНИЛА ФАЙЛ: {name}")
            blind.append(name)
            continue
        write_mutant(path, mutated)
        try:
            caught, why = run(test)
        finally:
            revert(rel)
        if not caught:
            print(f"[СЛЕП] {name}\n        {test}\n        {why}")
            blind.append(name)
        else:
            print(f"[ok]   {name} -> сторож покраснел")
    print()
    if blind:
        print(f"СЛЕПЫХ СТОРОЖЕЙ: {len(blind)} из {len(MUTATIONS)}")
        return 1
    print(f"Все {len(MUTATIONS)} мутаций пойманы.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
