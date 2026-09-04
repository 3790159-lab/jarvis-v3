# -*- coding: utf-8 -*-
"""Мутационный гейт D2-2 «вилка вместо отсылки к владельцу» (sales-competence §4).

Мишени — РЕШЕНИЯ спеки, а не слова кода:
  1 подстановка вилки вообще жива;
  2 «ни одной услуги в клаузе» ОСТАВЛЯЕТ отсылку (иначе назовём цену SMM за
    фотосессию);
  3 «две услуги с равным весом» ОСТАВЛЯЕТ отсылку (не гадаем);
  4 срочная клауза НЕ получает ценовую вилку;
  5 мост uk/ru по основам слов жив (иначе русский ответ не найдёт украинскую
    услугу, и вилка не подставится ровно там, где нужна);
  6 из строки прайса берётся только ДЕНЕЖНАЯ ГОЛОВА, а не весь хвост.

Мишени 2 и 3 — ВСТРЕЧНЫЕ: они ломают не подстановку, а её ГРАНИЦУ. Без них
«подставить первую попавшуюся вилку» прошло бы позитивные сторожа.

Прогон: python scripts/mutate_sales_d2_2_range.py  (в worktree, дерево чистое)
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

D = "chatter/core/guardrails.py"
L = "chatter/core/langdetect.py"

G = "tests/test_sales_c1_lead_numbers.py"
GD = "tests/test_sales_c1_formula_dedup.py"
GL = "tests/test_sales_c1_reply_language.py"
GR = "tests/chatter/test_guardrails_redaction.py"

MUTATIONS = [
    # ── 1: подстановки вилки нет вовсе — отсылка возвращается ────────────
    ("D2-2 отменён — ценовая клауза снова уходит в отсылку", D,
     [(b"            if known_range:",
       b"            if False:")],
     GR + "::test_redaction_gives_the_range_instead_of_a_referral"),

    # ── 2 (встречная): услуга не названа, а вилку всё равно подставляем ──
    ("граница снята: неизвестной услуге подставляется чужая вилка", D,
     [(b"    if best[0] == 0:",
       b"    if False and best[0] == 0:")],
     GR + "::test_unknown_service_still_falls_back_to_the_old_formula"),

    # ── 3 (встречная): две услуги с равным весом — гадаем ────────────────
    ("граница снята: при двух услугах выбирается первая попавшаяся", D,
     [(b"    if len(scored) > 1 and scored[1][0] == best[0]:",
       b"    if False:")],
     GR + "::test_ambiguous_service_falls_back_to_the_old_formula"),

    # ── 4: срочная клауза получает ЦЕНОВУЮ вилку ─────────────────────────
    ("срочная клауза получает вилку цен — ответ не на тот вопрос", D,
     [(b'        if rule != "deadline":',
       b"        if True:")],
     GR + "::test_deadline_clause_never_gets_a_price_range"),

    # ── 5: мост uk/ru мёртв — основы больше не режутся ───────────────────
    ("мост uk/ru снят: основы не режутся, «ведение» не найдёт «ведення»", D,
     [(b"            out.add(tok[:5])",
       b"            out.add(tok)")],
     GR + "::test_range_substitution_speaks_the_language_of_the_reply"),

    # ── 6: в реплику течёт хвост строки прайса ───────────────────────────
    ("берём весь хвост строки прайса, а не денежную голову", D,
     [(b'        money = _MONEY_HEAD.match(m.group("value").strip())',
       b'        money = re.match(r"(.+)", m.group("value").strip())')],
     GR + "::test_price_line_tail_never_leaks_into_the_reply"),
]


def write_mutant(path: Path, text) -> None:
    """Записать мутанта с УНИКАЛЬНЫМ mtime. Всегда `write_bytes`.

    Принимает И байты, И строку: мета-сторож на гейты зовёт этот метод строкой,
    и суженная до байтов сигнатура ломает ЗАМЕР гейта, а не сам гейт — то есть
    выглядит как «гейт не проверен», что в этом доме опаснее красного.
    """
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
         "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env)
    out = (p.stdout or "") + (p.stderr or "")
    tail = out.strip().splitlines()[-1] if out.strip() else "(пусто)"
    return (p.returncode == 1 and "failed" in out,
            "rc=%d | %s" % (p.returncode, tail[:140]))


def revert(rel: str, original: bytes) -> None:
    path = ROOT / rel
    path.write_bytes(original)
    stamp = _MTIME_BASE + next(_mtime_seq)
    os.utime(path, (stamp, stamp))


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
        edits = [(o, n) if o in mutated
                 else (o.replace(b"\n", b"\r\n"), n.replace(b"\n", b"\r\n"))
                 for o, n in edits]
        missing = [old for old, _new in edits if old not in mutated]
        if missing:
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
            revert(rel, original)
        if path.read_bytes() != original:
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
    print("Все %d мутаций пойманы, файлы возвращены побайтово." % len(MUTATIONS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
