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
VOCAB = "chatter/onboard/vocabulary.py"

TB = "tests/test_onboard_brief.py"
TR = "tests/test_onboard_render.py"
TP = "tests/test_onboard_report.py"
TC = "tests/test_onboard_checks.py"
TD = "tests/test_onboard_drill_scenario.py"
TL = "tests/test_onboard_cli.py"
TC15 = "tests/test_onboard_checks_c15.py"

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

    # ── render.py: название услуги, Q21 против Q22 ────────────────────────
    # Решение владельца 17.08. Три условия замены — три способа ошибиться, и
    # каждое обязано держаться своим сторожем, иначе правило «берём длинное»
    # незаметно превратится в «сочиняем похожее».
    ("правило названий выключено — цена снова у сокращённой услуги", RENDER,
     [("        out.append(replace(svc, title=candidates[0]) if len(candidates) == 1 else svc)",
       "        out.append(svc)")],
     f"{TR}::test_a_service_title_is_completed_from_the_services_list"),

    ("префикс больше не обязателен — генератор переименовывает по похожести",
     RENDER,
     [("        candidates = [e for e in entries\n"
       "                      if e.casefold().startswith(low) and len(e) > len(title)]",
       "        candidates = [e for e in entries if len(e) > len(title)]")],
     f"{TR}::test_a_title_the_services_list_does_not_continue_is_left_alone"),

    # Прицел именно сюда, и это ЗАМЕР: на укорачивании эта мутация не краснеет
    # вовсе — короткий кандидат отсекается префиксом раньше длины. Условие
    # длины наблюдаемо только на РАВНОМ кандидате в другом регистре.
    ("длина больше не обязательна — заголовок переписан регистром клиента",
     RENDER,
     [("if e.casefold().startswith(low) and len(e) > len(title)]",
       "if e.casefold().startswith(low)]")],
     f"{TR}::test_an_equal_entry_never_rewrites_the_price_heading"),

    ("два кандидата — берём первый, то есть угадываем за владельца", RENDER,
     [("if len(candidates) == 1 else svc)", "if len(candidates) >= 1 else svc)")],
     f"{TR}::test_two_candidates_leave_the_heading_untouched"),

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

    # ── vocabulary/report: слова, которые слышит ЛИД ──────────────────────
    # Решения владельца 17.08 по итогам теста Артёма: омоним «орієнтир»
    # разводим словами (пункт 11), про название роли СПРАШИВАЕМ (пункт 5).
    ("«орієнтир» вернулся в заглушку адреса — C13 снова флаг на каждом клиенте",
     VOCAB,
     [('        "address", "Адреса, як нас знайти, паркування", None,',
       '        "address", "Адреса, орієнтир, паркування", None,')],
     f"{TR}::test_the_address_stub_does_not_reuse_the_word_orientir"),

    ("вопрос про название роли больше не задаётся", REPORT,
     [("    role_question = _text(report.get(\"role_wording_question\")).strip()\n"
       "    if role_question and role_question not in questions:\n"
       "        questions.append(role_question)\n", "")],
     f"{TP}::test_the_client_is_always_asked_how_to_call_the_role"),

    ("вопрос про роль встал первым — пропущенный адрес уехал вниз", REPORT,
     [("        questions.append(role_question)", "        questions.insert(0, role_question)")],
     f"{TP}::test_the_standing_questions_come_after_the_missing_ones"),

    ("вопрос про реальность SLA больше не задаётся", REPORT,
     [("    sla_question = _text(report.get(\"sla_reality_question\")).strip()\n"
       "    if sla_question and sla_question not in questions:\n"
       "        questions.append(sla_question)\n", "")],
     f"{TP}::test_the_client_is_asked_whether_the_promised_sla_is_real"),

    ("про SLA спрашивают, даже когда клиент срока не называл", REPORT,
     [('            SLA_REALITY_QUESTION_UK.format(sla=sla_value) if sla_value else None),',
       '            SLA_REALITY_QUESTION_UK.format(sla=sla_value or "—")),')],
     f"{TP}::test_no_sla_question_when_the_brief_never_named_a_deadline"),

    ("вопрос про роль перестал цитировать слово клиента", REPORT,
     [('            role=_text((fields.get("q16_owner_ref") or {}).get("value")).strip() or "—"),',
       '            role="—"),')],
     f"{TP}::test_the_role_question_quotes_the_clients_own_word"),

    # ── checks.py: C15, числа knowledge против чисел брифа ────────────────
    # Проверка заведена 18.08 разнесённой парой авторов. Обе мутации ниже —
    # это дефекты, которые пара нашла ЖИВЬЁМ, а не выдуманные случаи.
    ("нумерация подраздела снова считается числом прайса", CHECKS,
     [('            scan = re.sub(r"^\\s*#*\\s*\\d+[.)]\\s*", "", line) if _is_heading else line',
       "            scan = line")],
     f"{TC15}::test_c15_green_on_the_golden_client"),

    # Прицел ЗАМЕРЕН, а не угадан: подмену SLA ловит ветка бесчисловой формы,
    # а не сверка чисел — на ней эта мутация зеленела. Числовую ветку
    # различает дрейф процента предоплаты.
    ("числа раздела больше не сверяются с полем — дрейф цены проходит", CHECKS,
     [("                if number in brief_numbers:\n                    continue",
       "                if True:\n                    continue")],
     f"{TC15}::test_c15_red_when_the_prepayment_percent_drifted"),

    ("подмена SLA перестала ловиться — бесчисловая форма не сверяется", CHECKS,
     [("                if (unit, mult) in brief_durations:\n                    continue",
       "                if True:\n                    continue")],
     f"{TC15}::test_c15_red_when_the_sla_lost_two_thirds_of_itself"),

    ("бесчисловая форма больше не несёт множитель", CHECKS,
     [("        brief_numbers |= {mult for _, mult in brief_durations}", "        pass")],
     f"{TC15}::test_c15_green_when_the_sla_gains_a_digit_the_brief_did_not_have"),

    ("забракованное поле перестало быть «не состоялось» — молча пропускается", CHECKS,
     [("        (blockers if blocking else notes).append(reason)",
       "        notes.append(reason)")],
     f"{TC15}::test_c15_is_blocked_when_a_source_field_was_rejected_as_garbage"),

    # Склейка «пусто = забраковано», обе стороны (§4, поправка 18.08). Вердикт
    # у обоих случаев ОДИН (`garbage`), различает только `raw` — поэтому склейка
    # выглядит как честное чтение вердикта и не вызывает подозрений при вычитке.
    ("пустая ячейка снова блокирует — rc 2 у каждого клиента без акции", CHECKS,
     [('        return f"{field_id}: поле пусто ({reason}) — числа взять неоткуда", False',
       '        return f"{field_id}: поле пусто ({reason}) — числа взять неоткуда", True')],
     f"{TC15}::test_an_empty_cell_and_a_rejected_cell_are_not_the_same_state"),

    ("мусор в ячейке снова читается как пустота — ответ мимо проходит зелёным", CHECKS,
     [('        if raw:\n            return (f"{field_id}: ответ клиента забракован ({reason}) — сверять "',
       '        if False:\n            return (f"{field_id}: ответ клиента забракован ({reason}) — сверять "')],
     f"{TC15}::test_an_empty_cell_and_a_rejected_cell_are_not_the_same_state"),

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
