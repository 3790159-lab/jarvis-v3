"""DEV-26 для блока stop_all (замечания 17–22): ломаем каждую правку обратно.

Тест, оставшийся зелёным на сломанной реализации, ничего не охраняет. Здесь
это особенно легко проглядеть: половина проверок смотрит на ТЕКСТ страницы, а
такой тест радостно зеленеет, находя нужное слово где-нибудь ещё в разметке.
Мутации ниже написаны так, чтобы поймать именно это.

Прогон: python scripts/mutate_panels_stopall.py (дерево должно быть чистым).
"""
from __future__ import annotations

import os
import subprocess
import sys
from itertools import count
from pathlib import Path
from gate_guard import refuse_if_live_tree   # DEV-31: гейт мутирует только worktree

ROOT = Path(__file__).resolve().parents[1]   # работает и в worktree

# Python признаёт кэш байткода актуальным по паре (mtime в ЦЕЛЫХ секундах,
# размер). Две мутации одного файла с одинаковым размером в одну секунду
# неотличимы — вторая исполнится байткодом первой и отчитается как [ok],
# ничего не проверив. Лечим уникальным mtime на каждую запись (DEV-26).
_MTIME_BASE = 2_000_000_000
_mtime_seq = count()

TD = "app/routers/tamapi_dashboard.py"
UI = "app/routers/panels_ui.py"
JP = "app/routers/jarvis_panel.py"
TM = "app/services/tamapi_metrics.py"
T = "tests/chatter/test_panels_stopall_ux.py"


def write_mutant(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    stamp = _MTIME_BASE + next(_mtime_seq)
    os.utime(path, (stamp, stamp))


# (имя, файл, что заменить, на что, какой тест ОБЯЗАН покраснеть)
MUTATIONS = [
    # ── 21: порядок кнопок ──────────────────────────────────────────────
    ("21: разрушительная кнопка возвращается под палец", TD,
     """    <button class='btn' onclick="closeM('pausebox')">Скасувати</button>
    <button class='btn broken' onclick="act('stop_all confirm')">Так, зупинити</button></div>""",
     """    <button class='btn broken' onclick="act('stop_all confirm')">Так, зупинити</button>
    <button class='btn' onclick="closeM('pausebox')">Скасувати</button></div>""",
     f"{T}::test_cancel_comes_before_confirm"),

    # ── 18: имя действия ────────────────────────────────────────────────
    ("18: триггер снова преуменьшает («Пауза»)", TD,
     '"⏹ Зупинити всіх</button>")',
     '"⏸ Пауза</button>")',
     f"{T}::test_trigger_button_says_stop_all"),

    # ── 19: судьба входящих ─────────────────────────────────────────────
    ("19: строка про пишущих во время паузы исчезла", TD,
     "  <p>Хто напише під час паузи, відповіді не отримає.</p>\n",
     "",
     f"{T}::test_modal_says_what_happens_to_incoming"),

    # ── 20: счётчик ─────────────────────────────────────────────────────
    ("20: счётчик считает всех подряд", TM,
     """    return sum(
        1 for f in feed
        if not f["paused"]
        and not f["needs_you"]
        and (f["state"] or "") not in TERMINAL_STATES
    )""",
     "    return len(feed)",
     f"{T}::test_modal_shows_live_dialog_count"),

    ("20: счётчик прибит константой", TD,
     "    n_active = M.active_dialogs(db, now=now)",
     "    n_active = 2",
     f"{T}::test_count_reflects_data_not_a_constant"),

    ("20: ноль печатается как «0 діалогів»", TD,
     '                 else "Зараз бот нікого не веде — пауза ні на кого не вплине.")',
     '                 else f"Зараз у роботі: {plural_dialogs(0)}.")',
     f"{T}::test_zero_dialogs_is_its_own_sentence"),

    ("20: 11–14 ломают склонение", UI,
     '    if 11 <= tail <= 14:\n        word = "діалогів"',
     '    if False:\n        word = "діалогів"',
     # Без параметра: pytest экранирует кириллицу в id (д...), и точное
     # имя случая по строке не сматчить. Красного всего узла достаточно.
     f"{T}::test_ukrainian_plural"),

    # ── 22: выход из модалки ────────────────────────────────────────────
    ("22: окно перестало быть dialog для скринридера", TD,
     "<div class='modal' id='pausebox' role='dialog' aria-modal='true'",
     "<div class='modal' id='pausebox' aria-modal='true'",
     f"{T}::test_modal_is_a_dialog_for_assistive_tech"),

    ("22: Esc выключен", TD,
     "  if(e.key==='Escape'){ e.preventDefault(); closeM(box.id); return; }",
     "  if(false){ }",
     f"{T}::test_js_closes_on_escape"),

    ("22: тап по подложке больше не закрывает", TD,
     "     onclick=\"closeOnBackdrop(event,'pausebox')\"><div class='box'>",
     "     ><div class='box'>",
     f"{T}::test_js_closes_on_backdrop_tap"),

    ("22: ловушка Tab снята", TD,
     "  if(e.key!=='Tab') return;",
     "  return;",
     f"{T}::test_js_traps_tab_inside_the_open_modal"),

    ("22: фокус не возвращается на триггер", TD,
     "  if(lastFocus && lastFocus.focus) lastFocus.focus();\n",
     "",
     f"{T}::test_js_returns_focus_to_the_trigger"),

    # ── 17: слово рядом с цветом ────────────────────────────────────────
    ("17: у точки снова отобрали подпись", UI,
     """    return (f"<span class='dot {STATE_TONE[st]}'></span>"
            f"<span class='slab'>{esc(STATE_LABEL[st])}</span>")""",
     '    return f"<span class=\'dot {STATE_TONE[st]}\'></span>"',
     f"{T}::test_state_label_is_next_to_the_dot_not_instead"),

    ("17: строки фермы вернулись к голой точке", JP,
     '            f"<div>{dot_html(r.state)}{esc(r.label)}"',
     '            f"<div><span class=\'dot {_DOT.get(r.state,\'off\')}\'></span>{esc(r.label)}"',
     f"{T}::test_rows_differing_only_by_state_differ_in_text"),

    # ── охрана одобренного текста ───────────────────────────────────────
    ("одобренная фраза потеряла «ВСІМ»", TD,
     "  <p>Вона перестане відповідати <b>ВСІМ</b> лідам, доки ви не увімкнете її назад.",
     "  <p>Вона перестане відповідати лідам, доки ви не увімкнете її назад.",
     f"{T}::test_approved_wording_is_untouched"),
]


def run(test: str) -> bool:
    """True = тест зелёный.

    Кодировка задана ЯВНО: `text=True` берёт cp1251, где байт `0x98` НЕ
    ОПРЕДЕЛЁН, а он приходит из «И» (`D0 98`) и «‘» (`E2 80 98`). Одна
    заглавная «И» в выводе упавшего теста роняет читающий поток
    `UnicodeDecodeError`, вывод приходит пустым — и вердикт гейта меняется
    от буквы в чужом тексте ассерта (замерено 15.08 на mutate_ops_watchdog:
    «38 слепых из 100» при краснеющих сторожах).
    """
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
    for name, rel, old, new, test in MUTATIONS:
        path = ROOT / rel
        text = path.read_text(encoding="utf-8")
        if old not in text:
            print(f"[!] МУТАЦИЯ НЕ ПРИМЕНИЛАСЬ: {name} — фрагмент не найден")
            blind.append((name, "фрагмент не найден"))
            continue
        write_mutant(path, text.replace(old, new, 1))
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
