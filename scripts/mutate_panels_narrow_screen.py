"""DEV-26 для узкого экрана: ломаем перестройку таблиц обратно.

Основной сценарий панели — телефон. Дефект здесь не выглядит дефектом: страница
просто становится шире окна, и часть фактов уезжает вправо молча. 12.08 панель
Джарвіса на 390 px рисовалась в 626 px — за краем жили три из четырёх колонок
таблицы арок, и на скриншоте это выглядело опрятно.

Сторожа на такое легко написать слепыми: проверка `«min-width:0» in CSS`
пережила снятие самого правила, потому что подстрока осталась в соседних
селекторах. Поэтому мутации здесь бьют не по смыслу, а по конкретным правилам.

Прогон: python scripts/mutate_panels_narrow_screen.py (дерево должно быть чистым).
"""
from __future__ import annotations

import os
import subprocess
import sys
from itertools import count
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]   # работает и в worktree

# Уникальный mtime на каждую запись: Python признаёт кэш байткода актуальным по
# паре (mtime в целых секундах, размер), и две мутации одного размера в одну
# секунду неотличимы — вторая исполнится байткодом первой (DEV-26).
_MTIME_BASE = 2_000_000_000
_mtime_seq = count()

UI = "app/routers/panels_ui.py"
JP = "app/routers/jarvis_panel.py"
TD = "app/routers/tamapi_dashboard.py"
T = "tests/chatter/test_panels_web.py"

# (имя, файл, [(что заменить, на что), ...], какой тест ОБЯЗАН покраснеть)
MUTATIONS = [
    # ── подписи колонок в карточке ───────────────────────────────────────
    ("ячейка «Вік» в арках осталась без подписи", JP,
     [("<td data-l='Вік' class='sub'>", "<td class='sub'>")],
     f"{T}::test_every_secondary_cell_carries_its_column_label"),

    ("ячейка «Стадія» в ленте диалогов осталась без подписи", TD,
     [("<td data-l='Стадія'>", "<td>")],
     f"{T}::test_every_secondary_cell_carries_its_column_label"),

    ("ячейка «Термін» у ключей осталась без подписи", JP,
     [("<td data-l='Термін'>", "<td>")],
     f"{T}::test_every_secondary_cell_carries_its_column_label"),

    # ── шапка таблицы ────────────────────────────────────────────────────
    ("шапка арок вернулась из thead в голый tr", JP,
     [("<thead><tr><th>Гілка</th>", "<tr><th>Гілка</th>")],
     f"{T}::test_table_headers_live_in_thead"),

    ("шапка ленты диалогов вернулась в голый tr", TD,
     [('("<table><thead><tr><th>Лід</th><th>Стадія</th><th>Останнє</th></tr>"',
       '("<table><tr><th>Лід</th><th>Стадія</th><th>Останнє</th></tr>"')],
     f"{T}::test_table_headers_live_in_thead"),

    # ── сами правила раскладки ───────────────────────────────────────────
    ("медиазапрос узкого экрана не срабатывает никогда", UI,
     [("@media(max-width:620px){", "@media(max-width:0px){")],
     f"{T}::test_narrow_screen_rules_are_present"),

    ("подписи колонок в карточке не рисуются", UI,
     [("table td[data-l]::before{content:attr(data-l)",
       "table td[data-l]::before{content:''")],
     f"{T}::test_narrow_screen_rules_are_present"),

    ("снят ограничитель min-width с flex/grid-элементов", UI,
     [(".row>*,.grid>*,.funnel>*,.tile,.fstep{min-width:0}", "")],
     f"{T}::test_narrow_screen_rules_are_present"),

    ("ячейка таблицы снова не рвёт длинное слово", UI,
     [("padding:2px 0;overflow-wrap:anywhere}", "padding:2px 0}")],
     f"{T}::test_narrow_screen_rules_are_present"),

    # Подмена, которая выглядит как исправление и не исправляет ничего:
    # `break-word` не участвует в расчёте min-content, и карточка останется
    # ровно такой же широкой. Сторож обязан отличать эти два слова.
    ("перенос подменён на break-word — на ширину карточки он не влияет", UI,
     [("padding:2px 0;overflow-wrap:anywhere}", "padding:2px 0;overflow-wrap:break-word}")],
     f"{T}::test_narrow_screen_rules_are_present"),
]


def write_mutant(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    stamp = _MTIME_BASE + next(_mtime_seq)
    os.utime(path, (stamp, stamp))


def run(test: str) -> bool:
    """True = тест зелёный."""
    # encoding задан явно: под Windows `text=True` берёт cp1251, и первый же
    # кириллический ассерт в выводе pytest роняет читающий поток
    # UnicodeDecodeError. Прогон при этом «проходит», но вывод теряется —
    # диагностика слепнет ровно там, где мутация что-то нашла.
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
