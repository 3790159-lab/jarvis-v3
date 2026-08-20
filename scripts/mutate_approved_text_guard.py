"""DEV-26 на сверку одобренного текста с кодом и спекой.

Мишень здесь необычная — САМ СТОРОЖ. Сверка трёх мест не имеет прод-кода, в
который можно внести тихую поломку: весь её смысл живёт в извлечении и
сравнении. Значит и проверять надо способность СТОРОЖА краснеть, а мутации
бить по его собственной механике.

Каждая мутация ломает ровно один способ поймать расхождение, и её обязан
поймать тот из сторожей А2–А7, который на этот способ и заведён.

Прогон: python scripts/mutate_approved_text_guard.py (дерево должно быть чистым).
"""
from __future__ import annotations

import os
import subprocess
import sys
from itertools import count
from pathlib import Path

from gate_guard import refuse_if_live_tree   # DEV-31: гейт мутирует только worktree

ROOT = Path(__file__).resolve().parents[1]

# Уникальный mtime на каждую запись: кэш байткода признаётся актуальным по паре
# (mtime в целых секундах, размер), и две мутации одного размера в одну секунду
# неотличимы — вторая исполнилась бы байткодом первой (DEV-26).
_MTIME_BASE = 2_000_000_000
_mtime_seq = count()

G = "tests/test_approved_text_matches_spec.py"

MUTATIONS = [
    ("отсутствие фразы в коде перестало быть расхождением", G,
     [("    if n == 0:", "    if n < 0:")],
     f"{G}::test_a2_a_phrase_edited_in_the_code_turns_it_red"),

    ("спека и сторож больше не сверяются между собой", G,
     [("    if _norm(spec_phrase) != _norm(guard_const):",
       "    if False:")],
     f"{G}::test_a3_a_phrase_edited_in_the_spec_turns_it_red"),

    # Самая ценная: она превращает сверку по AST в сверку по тексту файла —
    # ровно тот дефект, ради слепоты к которому AST и выбран.
    ("сверка идёт по ТЕКСТУ файла, а не по тому, что исполняется", G,
     [("    return [n.value for n in ast.walk(tree)\n"
       "            if isinstance(n, ast.Constant) and isinstance(n.value, str)]",
       '    return [_read(root / entry.code, "файл кода")]')],
     f"{G}::test_a5_a_phrase_moved_from_code_into_a_comment_turns_it_red"),

    ("дублирование одобренного текста разрешено", G,
     [("    elif n > 1:", "    elif n > 2:")],
     f"{G}::test_a6_a_second_occurrence_in_the_code_turns_it_red"),

    # Две мутации на fail-closed: пропавший якорь и пропавшая константа обязаны
    # быть КРАСНЫМИ, а не «нечего сравнивать».
    ("пропавший якорь спеки стал зелёным", G,
     [('        return ["СПЕКА: %s" % exc]', "        return []")],
     f"{G}::test_a7_a_missing_anchor_turns_it_red"),

    ("пропавшая константа сторожа стала зелёной", G,
     [('        return ["СТОРОЖ: %s" % exc]', "        return []")],
     f"{G}::test_a7_a_missing_constant_turns_it_red"),
]


def write_mutant(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    stamp = _MTIME_BASE + next(_mtime_seq)
    os.utime(path, (stamp, stamp))


def run(test: str) -> bool:
    """True = тест зелёный."""
    # encoding задан явно: под Windows `text=True` берёт cp1251 и роняет
    # читающий поток на первом кириллическом ассерте — прогон «проходит», а
    # вывод теряется ровно там, где мутация что-то нашла.
    p = subprocess.run(
        [sys.executable, "-m", "pytest", test, "-q", "--no-header",
         "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    return p.returncode == 0


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
            green = run(test)
        finally:
            revert(rel)
        if green:
            print(f"[СЛЕП] {name}\n        {test} остался ЗЕЛЁНЫМ")
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
