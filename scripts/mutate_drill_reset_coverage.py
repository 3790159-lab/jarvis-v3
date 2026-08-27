"""Мутационный гейт арки «сброс покрывает ВСЕ таблицы контакта» (§6 спеки).

Мишени — РЕШЕНИЯ спеки, а не слова кода: четыре категории вместо двух списков,
три сироты по местам, отказ (а не предупреждение) со СВОИМ кодом, отказ и в
сухом прогоне, сверка ДО записи, и правило «ровно одна категория» в обе стороны.

Сторожей писал ДРУГОЙ автор, от текста спеки, реализации не видя. Гейт проверяет
не код, а ИХ: ломаем решение и требуем, чтобы покраснел НАЗВАННЫЙ сторож —
`pytest файл::имя`, а не файл целиком. Прогон по файлу зеленел бы за счёт
соседа, и карта «решение → сторож» врала бы при зелёном гейте.

Правки пишутся БАЙТАМИ. `Path.write_text` под Windows перевёл бы LF-файл в CRLF
целиком, и «мутация» превратилась бы в правку всего файла. Фрагменты выбраны
ASCII-ТОЛЬКО: cp1251 не знает части кириллических байт, и пойманная мутация
печаталась бы слепой.

Прогон: python scripts/mutate_drill_reset_coverage.py  (в worktree, дерево чистое)
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

# Уникальный mtime на каждую запись: Python признаёт кэш байткода актуальным по
# паре (mtime в целых секундах, размер), и две мутации одного размера в одну
# секунду неотличимы — вторая исполнилась бы байткодом первой (DEV-26).
_MTIME_BASE = 2_000_000_000
_mtime_seq = count()

DR = "scripts/drill_reset.py"
G = "tests/test_drill_reset_table_coverage.py"

MUTATIONS = [
    # ── три сироты по местам (§3) ─────────────────────────────────────────
    ("outgoing_queue выпала из _WIPE_TABLES", DR,
     [(b'                "outgoing_queue")', b'                )')],
     G + "::test_outgoing_queue_is_declared_wiped"),

    ("status_index выпала из _KEEP_TABLES", DR,
     [(b'                "status_index")', b'                )')],
     G + "::test_funnel_transitions_and_status_index_are_declared_kept"),

    ("funnel_transitions уехала в _WIPE вместо _KEEP", DR,
     [(b'                "funnel_transitions",\n', b'')],
     G + "::test_every_contact_id_table_of_the_live_schema_is_named_exactly_once"),

    # ── четвёртая категория (§3, «не исключение, а категория») ────────────
    ("contacts объявлена стираемой вместо приводимой к исходному", DR,
     [(b'_RESET_IN_PLACE = ("contacts",)', b'_RESET_IN_PLACE = ()'),
      (b'                "outgoing_queue")',
       b'                "outgoing_queue", "contacts")')],
     G + "::test_contacts_is_declared_reset_in_place_and_not_wiped"),

    # ── механизм: ОТКАЗ, а не предупреждение (§4) ─────────────────────────
    ("отказ выродился в предупреждение (прогон продолжается)", DR,
     [(b'            return RC_UNCOVERED_TABLE', b'            pass')],
     G + "::test_unknown_table_refuses_with_its_own_code_and_changes_nothing"),

    ("отказ переиспользует чужой код возврата", DR,
     [(b'RC_UNCOVERED_TABLE = 3', b'RC_UNCOVERED_TABLE = 2')],
     G + "::test_unknown_table_refuses_with_its_own_code_and_changes_nothing"),

    ("отказ перестал называть обе возможности", DR,
     [(b'                f" `_WIPE_TABLES` (\xd1\x81\xd1\x82\xd0\xb8\xd1\x80\xd0\xb0\xd1\x82\xd1\x8c \xd0\xb2\xd0\xbc\xd0\xb5\xd1\x81\xd1\x82\xd0\xb5 \xd1\x81 \xd0\xbf\xd0\xb5\xd1\x80\xd0\xb5\xd0\xbf\xd0\xb8\xd1\x81\xd0\xba\xd0\xbe\xd0\xb9) \xd0\xb8\xd0\xbb\xd0\xb8 \xd0\xb2"',
       b'                f" \xd0\xba\xd1\x83\xd0\xb4\xd0\xb0-\xd0\xbd\xd0\xb8\xd0\xb1\xd1\x83\xd0\xb4\xd1\x8c \xd0\xb8\xd0\xbb\xd0\xb8 \xd0\xb2"')],
     G + "::test_refusal_names_the_table_and_both_options"),

    # ── сверка идёт ВСЕГДА, не только под --apply (§6.8) ─────────────────
    ("сухой прогон перестал сверять покрытие", DR,
     [(b'        problems = coverage_problems(conn)',
       b'        problems = coverage_problems(conn) if a.apply else []')],
     G + "::test_dry_run_also_refuses_on_unknown_table"),

    # ── правило «ровно одна категория» в ОБЕ стороны (§6.1) ──────────────
    # Мишень ПЕРЕЦЕЛЕНА после первого прогона. Была «отключить runtime-детектор
    # дублей» — и сторож справедливо остался зелёным: он утверждает про САМИ
    # КАТЕГОРИИ (ни одна таблица не названа дважды), а не про детектор. Ломать
    # надо то, о чём сторож говорит, — данные.
    ("таблица названа СРАЗУ В ДВУХ категориях", DR,
     [(b'_RESET_IN_PLACE = ("contacts",)',
       b'_RESET_IN_PLACE = ("contacts", "messages")')],
     G + "::test_no_table_is_named_in_two_categories"),

    ("сверка перестала смотреть на contact_id (краснеет на глобальных)", DR,
     [(b'        if "contact_id" in cols:', b'        if True:')],
     G + "::test_the_live_schema_alone_does_not_trigger_the_refusal"),

    # ── состав берётся из СХЕМЫ, а не из литерала (§6.2) ─────────────────
    # Мишень ПЕРЕЦЕЛЕНА: `test_coverage_rule_reacts_to_a_table_that_no_literal_knows`
    # — САМОПРОВЕРКА сторожевого файла (он честно пишет это в докстринге): она
    # зовёт собственные помощники `_schema`/`_uncovered`, а не код реализации, и
    # покраснеть на мутации кода не может В ПРИНЦИПЕ. Ту же беду ловит сторож,
    # который реально зовёт `main()` на базе с незнакомой таблицей.
    ("состав таблиц взят из списков вместо живой схемы", DR,
     [(b'    names = [r[0] for r in conn.execute(\n'
       b'        "SELECT name FROM sqlite_master WHERE type=\'table\'"\n'
       b'        " AND name NOT LIKE \'sqlite_%\'")]',
       b'    names = [t for tables in _ALL_CATEGORIES.values() for t in tables]')],
     G + "::test_unknown_table_refuses_with_its_own_code_and_changes_nothing"),
]


def write_mutant(path: Path, text) -> None:
    """Записать мутанта с УНИКАЛЬНЫМ mtime. Всегда `write_bytes`."""
    blob = text if isinstance(text, (bytes, bytearray)) else str(text).encode("utf-8")
    path.write_bytes(blob)
    stamp = _MTIME_BASE + next(_mtime_seq)
    os.utime(path, (stamp, stamp))


def run(test: str) -> tuple[bool, str]:
    """(поймана ли мутация, чем ответил pytest).

    КРАСНОЕ — РОВНО `rc 1` плюс `failed`: сломавшая СБОР мутация отвечает
    rc 2/4/5, и критерий «не 0 значит покраснел» принял бы её за пойманную.
    """
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    p = subprocess.run(
        [sys.executable, "-m", "pytest", test, "-q", "--no-header",
         "-p", "no:cacheprovider", "-p", "no:randomly"],
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
            "ОТКАЗ: рабочее дерево грязное — откат мутаций сотрёт эти правки.\n" + out)


def main() -> int:
    refuse_if_live_tree(ROOT)
    assert_clean()
    blind = []
    for name, rel, edits, test in MUTATIONS:
        path = ROOT / rel
        original = path.read_bytes()
        mutated = original
        missing = [old for old, _new in edits if old not in mutated]
        if missing:
            # Не применившаяся мутация — НЕ «ok»: она не проверила ничего.
            print("[!] МУТАЦИЯ НЕ ПРИМЕНИЛАСЬ: %s — фрагмент не найден" % name)
            blind.append((name, "фрагмент не найден"))
            continue
        for old, new in edits:
            mutated = mutated.replace(old, new, 1)
        if mutated == original:
            print("[!] МУТАЦИЯ НИЧЕГО НЕ ИЗМЕНИЛА: %s" % name)
            blind.append((name, "файл не изменился"))
            continue
        write_mutant(path, mutated)
        try:
            caught, why = run(test)
        finally:
            revert(rel)
        after = path.read_bytes()
        if after != original:
            raise SystemExit("ОТКАТ НЕ ВЕРНУЛ ФАЙЛ ПОБАЙТОВО: %s" % rel)
        if not caught:
            print("[СЛЕП] %s\n        %s\n        %s" % (name, test, why))
            blind.append((name, test))
        else:
            print("[ok]   %s -> сторож покраснел" % name)
    print()
    if blind:
        print("СЛЕПЫХ СТОРОЖЕЙ: %d из %d" % (len(blind), len(MUTATIONS)))
        return 1
    print("Все %d мутаций пойманы, файл возвращён побайтово." % len(MUTATIONS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
