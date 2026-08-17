"""DEV-26 для правки украинских ЧАСОВ: ломаем предохранитель и требуем КРАСНОГО.

Предмет — числовой guardrail на живых клиентах. Правка `e6f10d45` научила его
видеть «години», и ночной прогон показал, ЧЕМ такая правка держится: мутация М2
(«убрать стем годин») прошла 66 ЗЕЛЁНЫХ — добавление стема не проверялось
ничем, кроме честного слова автора. Этот гейт превращает тот разовый замер в
повторяемый: каждая из четырёх сторон правки обязана иметь сторожа, который
краснеет, когда сторону снимают.

Четыре стороны, и они независимы:
  * ветка в `_TIME_UNIT` — числовые сроки в часах («за 12 годин»);
  * стем в `_TIME_UNIT_STEMS` — БЕСЧИСЛОВОЕ «за годину», отдельная ветка;
  * явные окончания вместо `\\w*` — «годинник» не должен стать единицей времени;
  * необязательное окончание — голая форма «годин» («6–10 годин») обязана жить.

Прогон: python scripts/mutate_ukrainian_hours.py (в worktree, дерево чистое)
"""
from __future__ import annotations

import os
import subprocess
import sys
from itertools import count
from pathlib import Path
from gate_guard import refuse_if_live_tree   # DEV-31: гейт мутирует только worktree

ROOT = Path(__file__).resolve().parents[1]

# Уникальный mtime на каждую запись: Python признаёт кэш байткода актуальным по
# паре (mtime в целых секундах, размер), и две мутации одного размера в одну
# секунду неотличимы — вторая исполнилась бы байткодом первой (DEV-26).
_MTIME_BASE = 2_000_000_000
_mtime_seq = count()

G = "chatter/core/guardrails.py"
T = "tests/test_guardrails_ukrainian_hours.py"

HOURS_BRANCH = '    r"годин(?:а|и|у|ою|і|ах|ам|ами)?|"\n'

MUTATIONS = [
    # ── сторона 1: числовой срок в часах ──────────────────────────────────
    ("украинские часы снова не единица времени (правка снята целиком)", G,
     [(HOURS_BRANCH, "")],
     f"{T}::test_unbacked_hour_deadline_is_seen_as_deadline"),

    # Парная: снятие обязано ронять и сравнение с русским — иначе «слепее
    # русского» замерялось бы одним тестом, и его падение приняли бы за шум.
    ("снятие часов делает украинский слепее русского", G,
     [(HOURS_BRANCH, "")],
     f"{T}::test_ukrainian_hours_are_not_blinder_than_russian"),

    # ── сторона 2: БЕСЧИСЛОВОЕ обещание ───────────────────────────────────
    # Это и есть М2 ночного прогона: 66 зелёных на снятом предохранителе.
    ('стем «годин» убран — «за годину» опять обеспечено чем угодно на «год»', G,
     [('        "годин",\n', "")],
     f"{T}::test_feeding_in_knowledge_does_not_back_a_bare_hours_promise"),

    # ЗАМЕР, а не догадка: второй мутации на этот стем НЕ ставим. Пара
    # «убрать стем → покраснеет test_bare_hour_claim_without_number_is_seen»
    # прогнана и дала 9 passed: бесчисловую ветку держит регулярка `_TIME_UNIT`,
    # а стем решает ДРУГОЙ вопрос — обеспечено ли обещание текстом базы.
    # Различающий случай ровно один — «годування» в базе (тест выше), и он же
    # был единственной находкой ночной мутации М2.

    # ── сторона 3: окончания ЯВНО, а не `\w*` ─────────────────────────────
    ("окончания снова через \\w* — годинник стал единицей времени", G,
     [('r"годин(?:а|и|у|ою|і|ах|ам|ами)?|', 'r"годин\\w*|')],
     f"{T}::test_non_time_words_are_not_time_units_at_all"),

    ("окончания через \\w* — числа рядом с прибором стали срочными", G,
     [('r"годин(?:а|и|у|ою|і|ах|ам|ами)?|', 'r"годин\\w*|')],
     f"{T}::test_non_time_words_do_not_mark_knowledge_numbers_as_deadline"),

    # ── сторона 4: окончание НЕОБЯЗАТЕЛЬНО ────────────────────────────────
    ("окончание стало обязательным — голая форма «годин» выпала", G,
     [('r"годин(?:а|и|у|ою|і|ах|ам|ами)?|', 'r"годин(?:а|и|у|ою|і|ах|ам|ами)|')],
     f"{T}::test_knowledge_hours_mark_their_numbers_as_deadline"),

    # ── граница: соседние единицы обязаны остаться под сторожем ───────────
    # Правка добавила ветку в общее выражение. Если бы она задела соседей,
    # узнать об этом было бы неоткуда — русские часы проверяет ДРУГОЙ тест, и
    # он обязан краснеть от снятия своей единицы, а не от нашей.
    ("русские часы выпали из единиц времени", G,
     [(r'r"\b(?:недел|час|месяц)\w*|', r'r"\b(?:недел|месяц)\w*|')],
     f"{T}::test_existing_time_units_keep_working"),
]


def write_mutant(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    stamp = _MTIME_BASE + next(_mtime_seq)
    os.utime(path, (stamp, stamp))


def run(test: str) -> tuple[bool, str]:
    """(поймана ли мутация, чем именно ответил pytest).

    КРАСНОЕ — РОВНО `rc 1` плюс `failed` в выводе: мутация, сломавшая СБОР,
    отвечает rc 2/4/5, и критерий «не 0 значит покраснел» принял бы её за
    пойманную, хотя ни одна проверка не выполнилась.

    encoding задан явно: `text=True` берёт cp1251, где байт `0x98` не
    определён, а он приходит из «И» и «‘» — одна буква в выводе упавшего
    теста роняет читающий поток, и пойманная мутация печатается слепой.
    """
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
    """Откат идёт через `git checkout --`, то есть НЕЗАКОММИЧЕННОЕ он сотрёт."""
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
        text = path.read_text(encoding="utf-8")
        mutated = text
        missing = [old for old, _new in edits if old not in mutated]
        if missing:
            # Не применившаяся мутация — это НЕ «ok»: она ничего не проверила.
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
