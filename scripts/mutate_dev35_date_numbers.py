"""DEV-26 для DEV-35: снимаем правило даты и требуем КРАСНОГО.

Предмет — предохранитель на ЖИВЫХ клиентах, поэтому мутации бьют в обе
стороны. Одна половина проверяет, что снятие правила возвращает подавление
(«чем полнее пересказ акции, тем вернее ответ проглотят»), вторая — что
расширение правила не делает сторожа слепым: дата не имеет права
«обеспечивать» соседние числа, а год при валюте обязан остаться ценой.

Прогон: python scripts/mutate_dev35_date_numbers.py (в worktree, дерево чистое)
"""
from __future__ import annotations

import os
import subprocess
import sys
from itertools import count
from pathlib import Path
from gate_guard import refuse_if_live_tree   # DEV-31: гейт мутирует только worktree

ROOT = Path(__file__).resolve().parents[1]

_MTIME_BASE = 2_000_000_000
_mtime_seq = count()

G = "chatter/core/guardrails.py"
T = "tests/test_guardrails_date_in_price_sentence.py"

MUTATIONS = [
    # ── снятие правила возвращает дефект ──────────────────────────────────
    ("правило даты снято — полный пересказ акции снова подавляется", G,
     [("    for pattern in (_NUM_IN_TIME_CONTEXT, _DATE_NUMBERS):",
       "    for pattern in (_NUM_IN_TIME_CONTEXT,):")],
     f"{T}::test_the_full_promo_sentence_is_not_a_price_finding"),

    ("день при месяце больше не дата — число дня сверяется с прайсом", G,
     [(r'    rf"|\b(\d{{1,2}})\s+{_MONTH_NAME}",              # 30 вересня', r'    rf"",')],
     f"{T}::test_a_day_at_a_ukrainian_month_is_a_date_not_a_price"),

    # ── расширение правила не имеет права ослеплять ───────────────────────
    ("год опознаётся без слова года — законная цена «2026 грн» стала датой", G,
     [(r'    rf"\b((?:19|20)\d{{2}})\s*{_YEAR_WORD}"          # 2026 року / 2026 г.',
       r'    rf"\b((?:19|20)\d{{2}})"')],
     f"{T}::test_a_year_shaped_number_used_as_a_PRICE_is_still_caught"),

    ("дата в предложении отменяет ценовую проверку целиком", G,
     [("        time_nums = _time_context_numbers(sentence)",
       "        if _DATE_NUMBERS.search(sentence):\n            continue\n"
       "        time_nums = _time_context_numbers(sentence)")],
     f"{T}::test_a_date_does_not_make_the_whole_sentence_supported"),

    ("все числа ценового предложения объявлены срочными", G,
     [("            is_time = num in time_nums", "            is_time = True")],
     f"{T}::test_an_invented_price_is_still_caught"),

    # ── правка не имеет права уехать в разбор БАЗЫ ────────────────────────
    ("разбор базы поехал: ценовой фрагмент отдаёт числа в срочные", G,
     [("        if _PRICE_CONTEXT.search(frag):\n            price |= nums",
       "        if _PRICE_CONTEXT.search(frag):\n            deadline |= nums")],
     f"{T}::test_the_knowledge_parser_is_untouched"),
]


def write_mutant(path: Path, text: str) -> None:
    path.write_text(text.lstrip("﻿"), encoding="utf-8")
    stamp = _MTIME_BASE + next(_mtime_seq)
    os.utime(path, (stamp, stamp))


def run(test: str) -> tuple[bool, str]:
    """(поймана ли мутация, чем ответил pytest). КРАСНОЕ — РОВНО rc 1 плюс
    `failed`: сломанный СБОР отвечает rc 2/4/5, и «не ноль значит покраснел»
    принял бы его за пойманную. encoding явный — cp1251 не знает байт 0x98."""
    p = subprocess.run(
        [sys.executable, "-m", "pytest", test, "-q", "--no-header",
         "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace")
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
            "Закоммить их и повтори прогон:\n" + out)


def main() -> int:
    refuse_if_live_tree(ROOT)
    assert_clean()
    blind = []
    for name, rel, edits, test in MUTATIONS:
        path = ROOT / rel
        text = path.read_text(encoding="utf-8-sig")
        mutated = text
        missing = [old for old, _new in edits if old not in mutated]
        if missing:
            print(f"[!] МУТАЦИЯ НЕ ПРИМЕНИЛАСЬ: {name} — фрагмент не найден")
            blind.append((name, "фрагмент не найден"))
            continue
        for old, new in edits:
            mutated = mutated.replace(old, new, 1)
        write_mutant(path, mutated)
        try:
            caught, why = run(test)
        finally:
            revert(rel)
        if not caught:
            print(f"[СЛЕП] {name}\n        {test}\n        {why}")
            blind.append((name, test))
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
