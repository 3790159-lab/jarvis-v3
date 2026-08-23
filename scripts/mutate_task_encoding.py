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
T = "tests/test_scheduled_task_encoding.py"

MUTATIONS = [
    ("имя выпало из таблицы ожидания — забытая задача перестала ожидаться", M,
     [('    "JarvisDrillNightly": PROTECTION_X_UTF8,\n', "")],
     f"{T}::test_expectation_table_is_the_literal_five_names"),

    ("исключение превращено в обязательство — решение владельца стёрто", M,
     [('    "JarvisIgTokenRefresh": PROTECTION_EXEMPT,',
       '    "JarvisIgTokenRefresh": PROTECTION_X_UTF8,')],
     f"{T}::test_expectation_table_is_the_literal_five_names"),

    ("незащищённая задача объявлена защищённой — весь предмет снят", M,
     [("                task=name, state=STATE_UNPROTECTED, reason=STATE_UNPROTECTED,",
       "                task=name, state=STATE_OK, reason=STATE_OK,")],
     f"{T}::test_task_without_x_utf8_is_unprotected_not_ok_not_missing"),

    ("защищённая объявлена незащищённой — сторож станет фоном", M,
     [("                task=name, state=STATE_OK, reason=STATE_OK,",
       "                task=name, state=STATE_UNPROTECTED, reason=STATE_UNPROTECTED,")],
     f"{T}::test_protected_task_is_ok"),

    ("исключённая задача объявлена нарушением — красное при полном порядке", M,
     [("                task=name, state=STATE_EXEMPT, reason=STATE_EXEMPT,",
       "                task=name, state=STATE_UNPROTECTED, reason=STATE_UNPROTECTED,")],
     f"{T}::test_exempt_task_without_x_utf8_is_exempt_and_not_a_violation"),

    ("пропажа задачи склеена с неожиданной — «что делать» потеряно", M,
     [("                task=name, state=STATE_MISSING,\n"
       "                reason=STATE_MISSING,",
       "                task=name, state=STATE_UNEXPECTED,\n"
       "                reason=STATE_UNEXPECTED,")],
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
     f"{T}::test_expected_task_absent_from_snapshot_is_missing"),
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
