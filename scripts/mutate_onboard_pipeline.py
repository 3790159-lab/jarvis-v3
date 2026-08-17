"""DEV-26 для арки `chatter.onboard`: снимаем решения арки и требуем КРАСНОГО.

Правило заведено владельцем 17.08 и стоит на трёх случаях одних суток, когда
зелёная сюита промолчала, а мутация нашла: стем «годин» в guardrails, номер
строки в красном C11, заход поиска коротких значений в отчёте. Каждый раз
сторож существовал — и бил в соседнюю проверку.

Мутируются РЕШЕНИЯ, а не слова: где кончается раздел, что считать ответом
клиента, кто имеет право быть дрил-контактом, чего стоит метка вычитки. По
одной мутации на решение, и у каждой назван ОДИН тест: если он не покраснел,
решение держится честным словом автора.

Покрыты пять модулей арки из шести. `render.py` взят двумя сторонами R9,
`report.py` — «третьим состоянием», `checks.py` — C11 и вердиктом, `brief.py` —
границей мусор-детектора, `drill_scenario.py` — отказом по контакту.
НЕ покрыт `__main__.py` сверх метки вычитки: `--diff` и печать отчёта сторожей
имеют, но их решения дешевле, и на них правило пока не тратим.

Прогон: python scripts/mutate_onboard_pipeline.py (в worktree, дерево чистое)
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

BRIEF = "chatter/onboard/brief.py"
RENDER = "chatter/onboard/render.py"
REPORT = "chatter/onboard/report.py"
CHECKS = "chatter/onboard/checks.py"
DRILL = "chatter/onboard/drill_scenario.py"
MAIN = "chatter/onboard/__main__.py"

TB = "tests/test_onboard_brief.py"
TR = "tests/test_onboard_render.py"
TP = "tests/test_onboard_report.py"
TC = "tests/test_onboard_checks.py"
TD = "tests/test_onboard_drill_scenario.py"
TL = "tests/test_onboard_cli.py"

MUTATIONS = [
    # ── brief.py: граница мусор-детектора ─────────────────────────────────
    # Правило ест ответы клиента, если сдвинуть его на волос. «9-18» даёт долю
    # 0.75, «Київ» — короткий и осмысленный; оба были живыми жертвами на
    # ревью, и оба обязаны остаться `ok`.
    ("порог числовой заглушки сдвинут — «9-18» стало мусором", BRIEF,
     [("_NUMERIC_STUB_RATIO = 0.8", "_NUMERIC_STUB_RATIO = 0.7")],
     f"{TB}::test_working_hours_written_as_a_range_survive"),

    ("длина перестала ограничивать правило — содержательный ответ стал мусором",
     BRIEF,
     [("    if len(stripped) > _NUMERIC_STUB_LEN:\n        return False\n", "")],
     f"{TB}::test_a_long_digits_only_answer_is_not_a_stub"),

    # ── render.py: R9, обе стороны ────────────────────────────────────────
    ("маркеры модальности сняты — «може залишатися до наступного дня» повисло",
     RENDER,
     [('    "може ", "можуть ", "можливо", "іноді", "подекуди", "у складних",\n'
       '    "в складних", "буває",\n', "")],
     f"{TR}::test_a_modal_promise_without_a_number_also_names_who_will_tell_the_exact_one"),

    ("адресат считается названным всегда — дописка не появляется никогда",
     RENDER,
     [("        if any(verb in neighbourhood for verb in _AUTHORITY_VERBS):",
       "        if True:")],
     f"{TR}::test_dangling_deadline_names_who_will_tell_the_exact_one"),

    # ── report.py: третье состояние ───────────────────────────────────────
    # Ответ с вердиктом `ok` исчезал без следа и без записи — не в разделе 1,
    # не в разделе 3, нигде. Отчёт при этом выглядел нормальным.
    ("вердикт «ok» больше не узнаётся — чистый ответ исчезает без следа",
     REPORT,
     [('        if verdict == "ok":', '        if verdict == "_ok":')],
     f"{TP}::test_every_clean_field_reaches_section_one"),

    # ── checks.py: где кончается раздел ───────────────────────────────────
    # C11 краснела бы на КАЖДОМ клиенте: прайс — это заголовок и сразу
    # подразделы, и по прежнему счёту раздел читался пустым. Эталон проходил
    # проверку по случайности — вводным абзацем.
    ("подраздел снова обрывает раздел — прайс читается пустым", CHECKS,
     [("            if depth <= level:", "            if depth >= level:")],
     f"{TC}::test_c11_green_when_every_section_has_content"),

    # ── checks.py: чего стоит метка вычитки ───────────────────────────────
    ("невычитанный отчёт снова даёт зелёный вердикт", CHECKS,
     [("    if not reviewed:\n        return RC_RED\n", "")],
     f"{TC}::test_green_without_reviewed_is_one"),

    ("непрогнанная проверка перестала перевешивать — rc 2 съеден", CHECKS,
     [("    if any(r.blocked for r in rows):\n        return RC_NOT_RUN\n", "")],
     f"{TL}::test_the_reviewed_mark_does_not_paint_over_a_check_that_never_ran"),

    # ── drill_scenario.py: кому уедет реплика сценария ────────────────────
    # Заготовка принимала ЛЮБОЙ контакт, включая живого клиента, и печатала
    # его в готовый сценарий. Отказ приезжал только с края стенда — после
    # того, как владельца позвали к телефону.
    ("контакт снова не сверяется со списком — сценарий примет живого клиента",
     DRILL,
     [("    if str(contact) not in DRILL_CONTACTS:", "    if False:")],
     f"{TD}::test_contact_outside_the_drill_lists_is_refused"),

    # ── __main__.py: метка защищает сборку от переписывания ───────────────
    ("пересборка поверх метки вычитки снова разрешена", MAIN,
     [("    if is_reviewed(out_dir):", "    if False:")],
     f"{TL}::test_a_rebuild_over_the_reviewed_mark_is_refused"),
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
