"""DEV-26 для части A: ломаем иерархию, шкалу кеглей, цвет и пустые состояния.

Правила вёрстки — самый удобный дом для слепого сторожа. Проверка «`ans` есть
в разметке» переживает превращение ответа в обычный заголовок; проверка
«`min-width:0` встречается в CSS» пережила снятие самого правила (12.08).
Поэтому мутации ниже бьют не по словам, а по конкретным решениям: порядок
блоков, класс погашенной плитки, цвет точки, порог «двух точек» у графика.

Каждая мутация ОБЯЗАНА покраснить названный тест. Парность важна отдельно:
у «график из одной точки не рисуется» есть мутация в обе стороны, иначе
«не рисуем» тихо станет «не рисуем никогда».

Прогон: python scripts/mutate_panels_hierarchy.py (дерево должно быть чистым).
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
FARM = "app/services/jarvis_farm.py"
T = "tests/chatter/test_panels_hierarchy.py"

# Блок статуса целиком — для мутации «порядок блоков». Переставляем его ВЫШЕ
# долга и денег, то есть возвращаем ровно ту раскладку, с которой начали.
STATUS_BLOCK = """<h2>Стан</h2>
<div class='card statusline'>
  <div class='row'><span><span class='dot {st['dot']}'></span>
    <b>{esc(st['title'])}</b> <span class='sub'>· {esc(st['sub'])}
    · heartbeat {esc(hb_txt)}</span></span>{pause_btn}</div>
</div>

"""

# (имя, файл, [(что заменить, на что), ...], какой тест ОБЯЗАН покраснеть)
MUTATIONS = [
    # ── шкала кеглей ─────────────────────────────────────────────────────
    ("в CSS вернулся седьмой кегль", UI,
     [(".tile .v{font-size:24px", ".tile .v{font-size:21px")],
     f"{T}::test_the_type_scale_has_four_sizes_and_no_near_duplicates"),

    ("главный экран протащил свой кегль инлайном", TD,
     [("<h1 class='ans {tone}'>{esc(ans)}</h1>",
       "<h1 class='ans {tone}' style='font-size:20px'>{esc(ans)}</h1>")],
     f"{T}::test_no_page_smuggles_its_own_type_size[/panel/tamapi]"),

    ("пустая дельта протащила свой кегль инлайном", UI,
     [("""        return "<div class='d na'>нема з чим порівняти</div>\"""",
       """        return "<div class='d' style='font-size:13px'>нема з чим порівняти</div>\"""")],
     f"{T}::test_the_empty_state_markup_keeps_the_scale"),

    ("пустая плитка протащила свой кегль инлайном", TD,
     [("""            val = "<div class='v na'>історія накопичується</div>\"""",
       """            val = "<div class='v' style='font-size:13px'>історія накопичується</div>\"""")],
     f"{T}::test_the_empty_state_markup_keeps_the_scale"),

    ("подпись оси графика вернулась к 11 единицам", UI,
     [("fill='#98a2b3' font-size='12' ", "fill='#98a2b3' font-size='11' ")],
     f"{T}::test_no_page_smuggles_its_own_type_size[/panel/tamapi/dynamics]"),

    # ── ответ сверху ─────────────────────────────────────────────────────
    ("ответ снова стал заголовком страницы", TD,
     [("<h1 class='ans {tone}'>{esc(ans)}</h1>", "<h1>Ольга · TAMAPI</h1>")],
     f"{T}::test_the_answer_stands_first_and_largest"),

    ("ответ перестал считать очередь", TD,
     [('        return leads_waiting(len(fresh)), "wait"',
       '        return "Все спокійно", "calm"')],
     f"{T}::test_the_answer_counts_what_waits_for_you"),

    ("обрыв связи перестал перебивать очередь", TD,
     [('    if st["code"] == "down":\n        return "Немає зв\'язку з Ольгою", "broken"',
       '    if False:\n        return "Немає зв\'язку з Ольгою", "broken"')],
     f"{T}::test_a_dead_heartbeat_outranks_everything_in_the_answer"),

    ("панель Джарвиса снова начинается со списка", JP,
     [("<h1 class='ans {ans.tone}'>{esc(ans.text)}</h1>", "<h1>Панель Джарвіса</h1>")],
     f"{T}::test_jarvis_panel_answers_before_it_lists"),

    # ── порядок и статус-строка ──────────────────────────────────────────
    ("статус вернулся на самый верх, выше долга и денег", TD,
     [(STATUS_BLOCK, ""), ("{over_note}\n", STATUS_BLOCK + "{over_note}\n")],
     f"{T}::test_debt_comes_before_money_and_money_before_status"),

    ("статус снова разложен в два этажа", TD,
     [("""    <b>{esc(st['title'])}</b> <span class='sub'>· {esc(st['sub'])}
    · heartbeat {esc(hb_txt)}</span></span>{pause_btn}</div>""",
       """    <b>{esc(st['title'])}</b></span>{pause_btn}</div>
  <div class='sub'>{esc(st['sub'])} · heartbeat {esc(hb_txt)}</div>""")],
     f"{T}::test_the_status_card_is_a_single_line"),

    ("возраст heartbeat снова обрезается многоточием", TD,
     [("<div class='row'><span><span class='dot {st['dot']}'></span>",
       "<div class='row'><span class='ell'><span class='dot {st['dot']}'></span>")],
     f"{T}::test_the_heartbeat_age_is_not_truncated_away"),

    # ── карточка «Требує вас» ────────────────────────────────────────────
    ("все четыре действия снова на виду", TD,
     [("""        "<details class='more'><summary class='btn sm' title='Інші дії'>⋯</summary>"
        "<div class='menu'>"
""", ""),
      ("""        "</div></details></span></div></div>")""",
       """        "</span></div></div>")""")],
     f"{T}::test_only_one_action_stays_visible_the_rest_hide_under_dots"),

    ("карточка снова в четыре этажа", TD,
     [("\"<div class='row' style='margin-top:6px'>\"",
       "\"<div style='margin-top:6px'>\"")],
     f"{T}::test_attention_card_is_two_rows"),

    # ── ГРАНИЦА: долги остаются на виду ──────────────────────────────────
    ("сколько человек ждёт — уехало с карточки", TD,
     [("""        f" · чекає {esc(ago(it['last_ts']))}{dead}</span></div>\"""",
       """        f"{dead}</span></div>\"""")],
     f"{T}::test_both_ages_survive_the_two_line_card"),

    ("счётчик застарелых свернулся молча", TD,
     [("f\"<summary>Застарілі ({len(stale)}) · старші за 48 годин</summary>\"",
       "\"<summary>Застарілі</summary>\"")],
     f"{T}::test_the_stale_counter_and_the_dead_mark_stay_on_screen"),

    ("пометка мёртвого лида пропала из свёртки", TD,
     [('            + (f" · {DEAD_MARK}" if it.get("dead") else "")', '            + ""')],
     f"{T}::test_the_stale_counter_and_the_dead_mark_stay_on_screen"),

    # ── цвет по смыслу ───────────────────────────────────────────────────
    ("зелёный вернулся к «всё в норме»", UI,
     [(".dot.calm{background:var(--calm)}", ".dot.calm{background:var(--ok)}")],
     f"{T}::test_each_meaning_owns_its_colour[--ok-money]"),

    ("янтарь достался чему-то помимо ожидания", UI,
     [(".slab{font-size:12px;color:var(--dim)",
       ".slab{font-size:12px;color:var(--warn)")],
     f"{T}::test_each_meaning_owns_its_colour[--warn-wait]"),

    ("кнопка «Зупинити всіх» снова красная", TD,
     [("        pause_btn = (\"<button class='btn' onclick='pauseAsk()'>\"",
       "        pause_btn = (\"<button class='btn broken' onclick='pauseAsk()'>\"")],
     f"{T}::test_stop_all_is_neutral_and_red_lives_in_the_confirmation"),

    ("исчерпанный пакет снова выглядит аварией", TD,
     [('    bar_cls = "bar" + (" wait" if pkg["pct"] >= 80 else " money")',
       '    bar_cls = "bar" + (" broken" if pkg["pct"] >= 80 else " money")')],
     f"{T}::test_nothing_is_red_while_nothing_is_broken"),

    ("красный кружок вернулся к лиду, который просто ждёт", TD,
     [('        badge = "🟠 " if it["needs_you"]', '        badge = "🔴 " if it["needs_you"]')],
     f"{T}::test_nothing_is_red_while_nothing_is_broken"),

    ("авария связи перестала быть красной", TD,
     [('return {"code": "down", "dot": "broken"',
       'return {"code": "down", "dot": "calm"')],
     f"{T}::test_broken_link_is_red"),

    # ── пустые состояния ─────────────────────────────────────────────────
    ("погашенная плитка снова кликается", TD,
     [("        if s.total is None or not s.available:", "        if False:")],
     f"{T}::test_a_tile_without_history_is_dimmed_and_not_clickable"),

    ("на месте дельты снова прочерк", UI,
     [("""        return "<div class='d na'>нема з чим порівняти</div>\"""",
       """        return "<div class='d flat'>—</div>\"""")],
     f"{T}::test_a_missing_delta_says_why_instead_of_drawing_a_dash"),

    ("суточный лимит снова печатается пустотой", TD,
     [("""    cap_txt = (f"добовий ліміт: {esc(pkg['daily_cap'])}"
               if pkg["daily_cap"] is not None else "конфіг клієнта не прочитано")""",
       """    cap_txt = f"добовий ліміт: {esc(pkg['daily_cap'])}\"""")],
     f"{T}::test_an_unreadable_client_config_says_so_instead_of_an_empty_value"),

    # Парная: заглушка обязана называть причину. Возврат к «ліміт не заданий»
    # — не косметика: это спокойная формулировка там, где экран не знает о
    # клиенте ничего, то есть тревога, замаскированная под настройку.
    ("заглушка снова врёт спокойной формулировкой", TD,
     [("""else "конфіг клієнта не прочитано")""",
       """else "добовий ліміт не заданий")""")],
     f"{T}::test_an_unreadable_client_config_says_so_instead_of_an_empty_value"),

    ("график из одной точки снова рисуется", UI,
     [("    return sum(1 for _, v in s.points if v is not None) >= 2",
       "    return sum(1 for _, v in s.points if v is not None) >= 1")],
     f"{T}::test_a_single_point_series_is_not_drawn_as_a_chart"),

    # Парная к предыдущей: «не рисуем одну точку» не имеет права превратиться
    # в «не рисуем ничего» — такой отказ выглядит на экране одинаково.
    ("порог точек поднят и график исчез совсем", UI,
     [("    return sum(1 for _, v in s.points if v is not None) >= 2",
       "    return sum(1 for _, v in s.points if v is not None) >= 3")],
     f"{T}::test_two_points_still_draw"),

    # ══════════ заход 1: ответ по лестнице, аномалии, быстрое/медленное ══════
    #
    # Мутации бьют по РЕШЕНИЯМ, а не по словам: порядок уровней, порог ключа,
    # правило «аномалия или состояние», изоляция медленного сбора. Каждая пара
    # «сделали строже / сделали мягче» стоит рядом — односторонняя проверка
    # порога зеленеет на выключенном пороге.

    ("внешний сторож снова стал ответом", JP,
     [('    procs = list(fast["processes"])',
       '    if fast["external"].state != "ok":\n'
       '        return Answer(0, "Зовнішній сторож не налаштований", "broken", "")\n'
       '    procs = list(fast["processes"])')],
     f"{T}::test_an_always_true_condition_never_becomes_the_answer"),

    ("слепой сборщик перестал перебивать всё", JP,
     [('    if any(r.key == "procs" for r in procs):', '    if False:')],
     f"{T}::test_a_blind_collector_outranks_everything"),

    ("упавший раннер перестал перебивать сторожей", JP,
     [('    if any(r.key == "chatter" for r in bad):',
       '    if False and any(r.key == "chatter" for r in bad):')],
     f"{T}::test_a_dead_runner_outranks_a_lying_guardian"),

    ("падение с живым гардианом снова неотличимо от сиротского", JP,
     [('        if orphan:\n'
       '            return Answer(3, f"Впало: {len(bad)}, сам не підніметься", "broken",\n'
       '                          _lift_line(bad, guards))\n'
       '        return Answer(3, f"Впало: {len(bad)}, підніметься сам", "wait",\n'
       '                      _lift_line(bad, guards))',
       '        return Answer(3, f"Впало: {len(bad)}, сам не підніметься", "broken",\n'
       '                      _lift_line(bad, guards))')],
     f"{T}::test_a_fallen_process_reads_differently_when_a_guardian_is_alive"),

    ("ETA гардиана исчезла из второй строки", JP,
     [('            eta_txt = f", ~{eta} с" if eta else ""', '            eta_txt = ""')],
     f"{T}::test_the_second_line_names_the_guardian_and_the_eta"),

    ("спокойный ответ перестал перечислять проверенное", JP,
     [('    return Answer(6, "Ферма ціла", "calm", _checked_line(fast, slow))',
       '    return Answer(6, "Ферма ціла", "calm", "")')],
     f"{T}::test_the_calm_answer_lists_what_was_checked"),

    # Порог ключа — пара. 7 в ОТВЕТ, 30 в аномалии (решение владельца 14.08).
    ("порог ключа в ответе поднят до месяца", FARM,
     [("KEY_EXPIRY_ANSWER = 7", "KEY_EXPIRY_ANSWER = 30")],
     f"{T}::test_a_key_reaches_the_answer_only_under_seven_days"),

    ("порог ключа в ответе опущен до нуля", FARM,
     [("KEY_EXPIRY_ANSWER = 7", "KEY_EXPIRY_ANSWER = 0")],
     f"{T}::test_a_key_reaches_the_answer_only_under_seven_days"),

    ("непрочитанное медленное снова молчит", JP,
     [('        slow_note = "задачі, арки, ключі: ще не зчитані"', '        slow_note = ""')],
     f"{T}::test_a_stale_slow_cache_skips_the_level_and_says_so"),

    # Правило «аномалия или состояние» — пара в обе стороны: и «выделено всё»,
    # и «не выделено ничего» выглядят на экране одинаково опрятно.
    ("аномалией стало всё подряд", FARM,
     [('    if kind in ("process", "guardian"):\n        return item.state != "ok"',
       '    if kind in ("process", "guardian"):\n        return True')],
     f"{T}::test_healthy_rows_stay_off_the_first_screen"),

    ("аномалий не стало вовсе", FARM,
     [('    if kind in ("process", "guardian"):\n        return item.state != "ok"',
       '    if kind in ("process", "guardian"):\n        return False')],
     f"{T}::test_an_anomaly_is_never_hidden_in_the_state_block"),

    ("отставленный снайпер снова требует внимания", FARM,
     [('        return item.state not in ("ok", "off")', '        return item.state != "ok"')],
     f"{T}::test_a_retired_task_is_not_an_anomaly"),

    ("грязное дерево перестало быть аномалией", FARM,
     [('        return bool(item.get("dirty"))', '        return False')],
     f"{T}::test_a_dirty_worktree_is_an_anomaly_and_its_age_is_not"),

    ("аномалией стала любая ветка, включая просто старую", FARM,
     [('        return bool(item.get("dirty"))', '        return True')],
     f"{T}::test_a_dirty_worktree_is_an_anomaly_and_its_age_is_not"),

    ("первый экран снова ждёт git и PowerShell", JP,
     [("    slow = F.slow_cached()", "    slow = F.snapshot_slow()")],
     f"{T}::test_the_panel_answers_while_git_and_powershell_hang"),

    ("порог второй колонки снова привязан к устройству", UI,
     [("minmax(min(340px,100%),1fr)", "minmax(min(760px,100%),1fr)")],
     f"{T}::test_the_second_column_appears_by_content_not_by_device"),

    # Парная: колонка снова разучилась сжиматься. На 344 px (внешний экран
    # Fold) страница уезжала вбок на 32 px, и выглядело это опрятно.
    ("колонка снова не умеет быть уже своего минимума", UI,
     [("minmax(min(340px,100%),1fr)", "minmax(340px,1fr)")],
     f"{T}::test_the_second_column_appears_by_content_not_by_device"),

    ("слот заходa 2 вернулся пустой рамкой", JP,
     [("<div class='sub second'>{esc(ans.second)}</div>",
       "<div class='sub second'>{esc(ans.second)}</div>\n"
       "<div class='note'>поки тебе не було: —</div>")],
     f"{T}::test_no_empty_slot_pretends_there_were_no_events"),

    # Группировка (принцип Sentry) — пара: и «выложили списком», и «свернули
    # всё подряд» одинаково опрятны на вид.
    ("одинаковые аномалии снова выкладываются списком", JP,
     [("GROUP_FROM = 3", "GROUP_FROM = 999")],
     f"{T}::test_a_bulk_anomaly_is_grouped_with_a_counter"),

    ("под счётчик уехали даже две строки", JP,
     [("GROUP_FROM = 3", "GROUP_FROM = 1")],
     f"{T}::test_a_couple_of_anomalies_are_not_hidden_behind_a_counter"),

    ("служебная ширина экрана исчезла из футера", JP,
     [("<div class='sub'>ширина екрана: <span id='vw'>—</span> px</div>", "")],
     f"{T}::test_the_viewport_width_is_printed_for_the_next_layout_pass"),
]



def write_mutant(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    stamp = _MTIME_BASE + next(_mtime_seq)
    os.utime(path, (stamp, stamp))


def run(test: str) -> bool:
    """True = тест зелёный."""
    # encoding задан явно: под Windows `text=True` берёт cp1251, и первый же
    # кириллический ассерт в выводе pytest роняет читающий поток
    # UnicodeDecodeError — диагностика слепнет там, где мутация что-то нашла.
    p = subprocess.run(
        [sys.executable, "-m", "pytest", test, "-q", "--no-header",
         "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    if "no tests ran" in p.stdout or "ERROR" in p.stdout[:400]:
        # Тест не нашёлся по имени — это не «сторож поймал», это опечатка в
        # адресе. Считаем зелёным, чтобы мутация попала в отчёт как слепая.
        return True
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
