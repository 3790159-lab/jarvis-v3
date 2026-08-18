"""DEV-26 для правил ответа (спека 2026-08-17-reply-quality-four-rules, §5).

Правила ответа — тот случай, где зелёная сюита особенно легко врёт. Поведение
модели тестами не ловится, значит сторожа стоят на СБОРКЕ; а сборку можно
«упростить» так, что тесты останутся зелёными, а правило перестанет доезжать.
Каждая мутация ниже — именно такое упрощение, а не поломка:

  M1 — правило не доезжает до промпта (сборка «забыла» строку);
  M2 — формулировку переписали копией по месту (два источника вместо одного);
  M3 — оборот претензии выпал из списка;
  M4 — правило претензии сняли целиком;
  M5 — карточка называет чужую причину (тег старого слоя вместо своего);
  M6 — список ловит ВСЁ (нейтральная передача тоже подавляется);
  M7 — история диалога подмешана в базу обеспеченных фактов.

Прогон: python scripts/mutate_reply_rules.py (в worktree, дерево чистое)
"""
from __future__ import annotations

import os
import subprocess
import sys
from itertools import count
from pathlib import Path
from gate_guard import refuse_if_live_tree   # DEV-31: гейт мутирует только worktree

ROOT = Path(__file__).resolve().parents[1]

# Уникальный mtime на каждую запись (jarvis-dev26-gate-stale-pyc).
_MTIME_BASE = 2_000_000_000
_mtime_seq = count()

BRAIN = "chatter/core/brain.py"
RULES = "chatter/core/reply_rules.py"
ESC = "chatter/core/escalation.py"
RUN = "chatter/run.py"

T = "tests/chatter/test_reply_quality_rules.py"

# Копия правила «по месту» — ровно то, как выглядит невинная правка: человек
# видит вызов функции, разворачивает его в строку, и с этого момента редакций
# две. Фрагмент дословный, поэтому сторож одного источника его увидит.
_INLINE_COPY = (
    '    parts.append("\\n" + "Про МАРШРУТ припускати можна: «це до старшого '
    'майстра», «він підтвердить», «уточню в нього» — таке веде до людини.")'
)

MUTATIONS = [
    ("M1 правило не доезжает до системного слоя", BRAIN,
     [('    parts.append("\\n" + reply_rules.prompt_rules_block())', "    pass")],
     f"{T}::test_assumption_boundary_rule_reaches_every_clients_system_prompt"),

    ("M2 формулировку переписали копией по месту — источников стало два", BRAIN,
     [('    parts.append("\\n" + reply_rules.prompt_rules_block())', _INLINE_COPY)],
     f"{T}::test_each_rule_text_lives_in_exactly_one_source_file"),

    ("M3 оборот претензии выпал из списка", RULES,
     [('    "вирішить питання компенсації",\n', "")],
     f"{T}::test_a_complaint_promise_in_the_reply_is_caught_and_the_reply_is_suppressed"),

    ("M4 правило претензии снято целиком", ESC,
     [('    complaint = complaint_promise(reply or "")', "    complaint = None")],
     f"{T}::test_a_complaint_promise_in_the_reply_is_caught_and_the_reply_is_suppressed"),

    ("M5 карточка называет чужую причину — тег старого слоя", ESC,
     [("            tag=COMPLAINT_TAG,", '            tag="unbacked_promise",')],
     f"{T}::test_a_complaint_promise_in_the_reply_is_caught_and_the_reply_is_suppressed"),

    ("M6 список ловит ВСЁ — нейтральная передача тоже подавляется", RULES,
     [('    text = (reply or "").casefold()\n'
       "    for phrase in COMPLAINT_FORBIDDEN_UK:\n"
       "        if phrase in text:\n"
       "            return phrase\n"
       "    return None",
       "    return COMPLAINT_FORBIDDEN_UK[0] if (reply or '').strip() else None")],
     f"{T}::test_a_neutral_handoff_is_not_suppressed"),

    ("M7 история подмешана в базу обеспеченных фактов", RUN,
     [("        knowledge=cfg.knowledge, keywords=deps.escalation_keywords,",
       "        knowledge=cfg.knowledge + ' '.join(m['text'] for m in store.history(contact_id)),\n"
       "        keywords=deps.escalation_keywords,")],
     f"{T}::test_a_number_invented_in_a_previous_reply_does_not_become_backed"),
]

# Сторожа, которые обязаны остаться ЗЕЛЁНЫМИ на каждом мутанте. Мутация,
# роняющая всё подряд, ничего не доказывает — она просто ломает импорт.
ALWAYS_GREEN = [
    f"{T}::test_the_rule_names_both_halves_of_the_boundary",
]


def write_mutant(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    stamp = _MTIME_BASE + next(_mtime_seq)
    os.utime(path, (stamp, stamp))


def run(test: str) -> int:
    """Код возврата pytest. encoding задан явно: под Windows text=True берёт
    cp1251 и роняет читающий поток на первом кириллическом ассерте."""
    p = subprocess.run(
        [sys.executable, "-m", "pytest", test, "-q", "--no-header",
         "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    return p.returncode


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
        text = path.read_text(encoding="utf-8")
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
            rc = run(test)
            # «Красный» — это РОВНО rc 1. rc 2+ означает, что мутант сломал
            # сборку или сбор тестов, и сторож ничего не доказал
            # (jarvis-mutation-gate-false-green-modes).
            if rc == 1:
                still_green = [g for g in ALWAYS_GREEN if run(g) != 0]
                if still_green:
                    print(f"[!] {name}: мутант уронил и контроль {still_green} — "
                          f"это поломка, а не пойманная мутация")
                    blind.append((name, "уронил контрольные тесты"))
                else:
                    print(f"[ok]   {name} -> сторож покраснел")
            elif rc == 0:
                print(f"[!] СЛЕПОЙ СТОРОЖ: {name} -> {test} остался ЗЕЛЁНЫМ")
                blind.append((name, "остался зелёным"))
            else:
                print(f"[!] {name}: pytest вернул {rc} (не 1) — сторож не доказан")
                blind.append((name, f"rc {rc}"))
        finally:
            revert(rel)
    print()
    if blind:
        print(f"СЛЕПЫХ СТОРОЖЕЙ: {len(blind)} из {len(MUTATIONS)}")
        for name, why in blind:
            print(f"  - {name}: {why}")
        return 1
    print(f"Все {len(MUTATIONS)} мутаций пойманы.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
