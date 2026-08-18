"""DEV-26 для DEV-32: ломаем опознание реплики без букв и требуем КРАСНОГО.

Acceptance тикета называет две мутации поимённо (пункты 3 и 4): снять запасной
путь и уронить порог. Обе здесь, плюс те способы сломать правку, которые видны
только изнутри реализации: запасной путь без порога, сравнение всё тех же
пустых строк, снятый отказ парсера и отказ без номера шага.

Почему мутация «порог» важна отдельно. Правка добавляет ВТОРОЙ путь сравнения,
и цена ошибки в нём обратная цене исходного дефекта: было ложное КРАСНОЕ
(судья не опознал), станет ложное ЗЕЛЁНОЕ (судья опознал не то) — а такое
красное молчит и уезжает в отчёт как «шаг прошёл».

BOM НЕ ставится: предмет мутации `.py`.

Прогон: python scripts/mutate_drill_judge_letterless.py (в worktree, дерево чистое)
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

D = "chatter/core/drill.py"
T = "tests/chatter/test_drill_judge_letterless.py"

MUTATIONS = [
    ("запасной путь снят — любая реплика без букв снова непознаваема", D,
     [("        raw = \" \".join((text or \"\").split())\n        if not raw:\n            return None",
       "        return None\n        raw = \" \".join((text or \"\").split())\n        if not raw:\n            return None")],
     f"{T}::test_an_emoji_only_step_is_recognised_as_itself"),

    ("запасной путь сравнивает всё те же нормализованные (пустые) строки", D,
     [('        scores = [SequenceMatcher(None, raw, " ".join((s.say or "").split())).ratio()\n'
       '                  for s in steps]',
       '        scores = [SequenceMatcher(None, raw, _normalize_say(s.say)).ratio()\n'
       '                  for s in steps]')],
     f"{T}::test_a_letterless_reply_matches_the_right_step_among_several"),

    ("ГЛАВНОЕ: порог снят — лучший кандидат побеждает всегда", D,
     [("    return best if scores[best] >= threshold else None",
       "    return best")],
     f"{T}::test_two_different_letterless_replies_do_not_collapse_into_one"),

    ("порог уронен до 0.1 — посторонняя фраза притворяется шагом", D,
     [("def match_step(text: str, steps, *, threshold: float = 0.72) -> int | None:",
       "def match_step(text: str, steps, *, threshold: float = 0.1) -> int | None:")],
     f"{T}::test_a_stranger_reply_still_does_not_pretend_to_be_a_step"),

    ("отказ парсера снят — неопознаваемый сценарий снова принимается", D,
     [("        got = match_step(step.say, ordered)\n        if got == i:\n            continue",
       "        got = match_step(step.say, ordered)\n        if True:\n            continue")],
     f"{T}::test_a_scenario_whose_step_cannot_identify_itself_is_refused"),

    ("отказ не называет, чем шаг перепутан — чинить нечего", D,
     [('        raise DrillScenarioError(\n            f"шаг {i + 1} «{step.say}» опознаётся как {where} — судья припишет "',
       '        raise DrillScenarioError(\n            f"сценарий не принят — судья припишет "')],
     f"{T}::test_the_refusal_names_both_steps"),

    ("отказ парсера бьёт по ЗДОРОВОМУ сценарию — стенд встал бы весь", D,
     [("        got = match_step(step.say, ordered)\n        if got == i:\n            continue",
       "        got = match_step(step.say, ordered)\n        if got == i and i < 0:\n            continue")],
     f"{T}::test_a_healthy_scenario_is_still_accepted"),

    # СНЯТА мутация «пустая реплика снова считается репликой»
    # (`raw = ... or " "` вместо раннего выхода). Прогон показал 1 passed:
    # различить её нечем, и это свойство предмета, а не сторожа. Пустую
    # реплику держит ПОРОГ — пробел против любой реальной реплики шага даёт
    # ratio 0.15 и ниже, а шага из одного пробела не существует
    # (`parse_scenario` требует непустой `say`). Мутация, которая ничего не
    # различает, зачлась бы гейту как работа и врала бы про покрытие.
]


def write_mutant(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    stamp = _MTIME_BASE + next(_mtime_seq)
    os.utime(path, (stamp, stamp))


def run(test: str) -> tuple[bool, str]:
    """(поймана ли мутация, чем ответил pytest). КРАСНОЕ — РОВНО rc 1 плюс
    `failed`: rc 2 это «тест не собрался»."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
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
        if mutated == text:
            print(f"[!] МУТАЦИЯ НИЧЕГО НЕ ИЗМЕНИЛА: {name}")
            blind.append((name, "текст не изменился"))
            continue
        write_mutant(path, mutated)
        try:
            caught, why = run(test)
        finally:
            revert(rel)
        if not caught:
            print(f"[СЛЕП] {name}\n        {test}\n        {why}")
            blind.append((name, test))
        else:
            print(f"[ok]   {name}")

    print(f"\nмутаций {len(MUTATIONS)}, поймано {len(MUTATIONS) - len(blind)}")
    if blind:
        print("СЛЕПЫЕ:")
        for name, where in blind:
            print(f"  - {name} ({where})")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
