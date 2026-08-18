"""DEV-26 для DEV-36: ломаем различение «свой сценарий / чужой» и требуем КРАСНОГО.

Предмет — единственная граница: C7 зеленеет по файлу, найденному ПО ИМЕНИ, а
не по происхождению. Сторож, не краснеющий на снятом различении, вернёт ровно
ту ошибку, ради которой заведён тикет: человека зовут на ПЛАТНЫЙ живой прогон
по сценарию месячной давности под другой прайс.

Отдельно сторожится обратная сторона (мутация «флаг накрывает всё»): мягкое
состояние обязано покрывать ТОЛЬКО происхождение. Флаг вердикт не роняет, и
проглоченный им нераспознанный сценарий стал бы тихим красным — дефект,
который читается как норма.

BOM здесь НЕ ставится: предмет мутации `.py`, а BOM в нём Python исполняет
молча, зато `ast.parse` краснеет — мутант «не применился» выглядел бы пойманным.

Прогон: python scripts/mutate_c7_scenario_origin.py (в worktree, дерево чистое)
"""
from __future__ import annotations

import os
import subprocess
import sys
from itertools import count
from pathlib import Path

from gate_guard import refuse_if_live_tree   # DEV-31: гейт мутирует только worktree

ROOT = Path(__file__).resolve().parents[1]

# Уникальный mtime каждому мутанту: DEV-26 ловил случай, когда pytest исполнял
# ЧУЖОЙ байткод и гейт зеленел на неизменённом коде.
_MTIME_BASE = 2_000_000_000
_mtime_seq = count()

C = "chatter/onboard/checks.py"
T = "tests/test_onboard_c7_scenario_origin.py"

MUTATIONS = [
    ("фолбэк объявляет себя выходом пайплайна — ложное зелёное вернулось", C,
     [('    return sorted(drills.glob(f"{ctx.slug}*.yaml")), "manual"',
       '    return sorted(drills.glob(f"{ctx.slug}*.yaml")), "build"')],
     f"{T}::test_a_foreign_manual_scenario_is_a_flag_and_never_a_plain_green"),

    ("ветка происхождения снята вовсе", C,
     [('    if origin != "build":', '    if False:')],
     f"{T}::test_a_foreign_manual_scenario_is_a_flag_and_never_a_plain_green"),

    ("флаг ставится ВСЕГДА — свой сценарий тоже перестал быть зелёным", C,
     [('    if origin != "build":', '    if True:')],
     f"{T}::test_own_scenario_in_the_build_dir_is_plain_green"),

    ("чужой сценарий снова зелёный, хоть и с оговоркой в тексте", C,
     [('        return CheckResult(\n            "C7", False, True,',
       '        return CheckResult(\n            "C7", True, True,')],
     f"{T}::test_a_foreign_manual_scenario_is_a_flag_and_never_a_plain_green"),

    ("оговорка перестала быть флагом и роняет вердикт на ручном эталоне", C,
     [('        return CheckResult(\n            "C7", False, True,',
       '        return CheckResult(\n            "C7", False, False,')],
     f"{T}::test_a_foreign_manual_scenario_is_a_flag_and_never_a_plain_green"),

    ("ГЛАВНОЕ: флаг выставлен ДО разбора — накрывает и настоящие дефекты", C,
     [("    paths, origin = _drill_scenarios(ctx)\n",
       "    paths, origin = _drill_scenarios(ctx)\n"
       "    if paths and origin != 'build':\n"
       "        return CheckResult('C7', False, True, 'ручной сценарий из "
       "docs/chatter/drills/', paths[0].name, None)\n")],
     f"{T}::test_a_real_defect_in_a_foreign_file_stays_red"),

    ("ГЛАВНОЕ: то же, вторая жертва: неразбираемый сценарий тонет во флаге", C,
     [("    paths, origin = _drill_scenarios(ctx)\n",
       "    paths, origin = _drill_scenarios(ctx)\n"
       "    if paths and origin != 'build':\n"
       "        return CheckResult('C7', False, True, 'ручной сценарий из "
       "docs/chatter/drills/', paths[0].name, None)\n")],
     f"{T}::test_an_unparsable_foreign_file_stays_red"),

    ("флаг не называет файл — человеку нечего открыть", C,
     [('            f"сценарий взят из docs/chatter/drills/ ({names}): это РУЧНОЙ "',
       '            f"сценарий взят из docs/chatter/drills/: это РУЧНОЙ "')],
     f"{T}::test_a_foreign_manual_scenario_is_a_flag_and_never_a_plain_green"),

    ("флаг не говорит, что сценарий РУЧНОЙ — остаётся «файл как файл»", C,
     [('            f"сценарий взят из docs/chatter/drills/ ({names}): это РУЧНОЙ "\n'
       '            f"сценарий, а не выход пайплайна — его нашли по совпадению имени "',
       '            f"сценарий разобран ({names}) — найден по совпадению имени "')],
     f"{T}::test_the_flag_names_where_the_file_came_from"),

    ("корень репозитория снова неподменяем — сторож происхождения слепнет", C,
     [("        self.repo_root = _REPO_ROOT",
       "        self.repo_root = Path(__file__).resolve().parents[2]")],
     f"{T}::test_no_scenario_at_all_is_red_and_not_a_flag"),
]


def write_mutant(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    stamp = _MTIME_BASE + next(_mtime_seq)
    os.utime(path, (stamp, stamp))


def run(test: str) -> tuple[bool, str]:
    """(поймана ли мутация, чем ответил pytest). КРАСНОЕ — РОВНО rc 1 плюс
    `failed`: rc 2 это «тест не собрался», и зачесть его себе значит объявить
    сломанный прогон пойманной мутацией."""
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
        text = path.read_text(encoding="utf-8")
        mutated = text
        missing = [old for old, _new in edits if old not in mutated]
        if missing:
            # НЕ засчитывается пойманной: мутация, которая не применилась,
            # ничего не проверила.
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
