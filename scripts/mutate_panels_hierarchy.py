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
PS1 = "scripts/chatter_guardian_detached.ps1"
T = "tests/chatter/test_panels_hierarchy.py"
FT = "tests/chatter/test_farm_self_match.py"
FD = "tests/chatter/test_panel_feed.py"
JV = "tests/chatter/test_panel_journal_view.py"

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
     [("<h1 class='ans {ans.tone}'>{esc(ans.text)}</h1>", "<h1>Панель Джарвиса</h1>")],
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
       '        return Answer(0, "Внешний сторож не настроен", "broken", "")\n'
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
       '            return Answer(3, f"Упало: {len(bad)}, сам не поднимется", "broken",\n'
       '                          _lift_line(bad, guards))\n'
       '        return Answer(3, f"Упало: {len(bad)}, поднимется сам", "wait",\n'
       '                      _lift_line(bad, guards))',
       '        return Answer(3, f"Упало: {len(bad)}, сам не поднимется", "broken",\n'
       '                      _lift_line(bad, guards))')],
     f"{T}::test_a_fallen_process_reads_differently_when_a_guardian_is_alive"),

    ("ETA гардиана исчезла из второй строки", JP,
     [('            eta_txt = f", ~{eta} с" if eta else ""', '            eta_txt = ""')],
     f"{T}::test_the_second_line_names_the_guardian_and_the_eta"),

    ("спокойный ответ перестал перечислять проверенное", JP,
     [('    return Answer(6, "Ферма цела", "calm", _checked_line(fast, slow))',
       '    return Answer(6, "Ферма цела", "calm", "")')],
     f"{T}::test_the_calm_answer_lists_what_was_checked"),

    # Порог ключа — пара. 7 в ОТВЕТ, 30 в аномалии (решение владельца 14.08).
    ("порог ключа в ответе поднят до месяца", FARM,
     [("KEY_EXPIRY_ANSWER = 7", "KEY_EXPIRY_ANSWER = 30")],
     f"{T}::test_a_key_reaches_the_answer_only_under_seven_days"),

    ("порог ключа в ответе опущен до нуля", FARM,
     [("KEY_EXPIRY_ANSWER = 7", "KEY_EXPIRY_ANSWER = 0")],
     f"{T}::test_a_key_reaches_the_answer_only_under_seven_days"),

    ("непрочитанное медленное снова молчит", JP,
     [('        slow_note = f"{_SLOW_PREFIX}: ещё не прочитаны"', '        slow_note = ""')],
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
     [("minmax(min(320px,100%),1fr)", "minmax(min(760px,100%),1fr)")],
     f"{T}::test_the_second_column_appears_by_content_not_by_device"),

    # Парная: колонка снова разучилась сжиматься. На 344 px (внешний экран
    # Fold) страница уезжала вбок на 32 px, и выглядело это опрятно.
    ("колонка снова не умеет быть уже своего минимума", UI,
     [("minmax(min(320px,100%),1fr)", "minmax(320px,1fr)")],
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
     [("<div class='sub'>ширина экрана: <span id='vw'>—</span> px</div>", "")],
     f"{T}::test_the_viewport_width_is_printed_for_the_next_layout_pass"),

    # ═══════ правки 14.08 (вечер): самоотрицание, склейка подписи, 707 px ═════
    #
    # Первая пара — про ГРАБЛЮ 1. Обе стороны обязаны быть под сторожем: и
    # «снова считаем упоминание запуском» (ферма зеленеет от диагностического
    # однострочника), и «снова судим по родству» (панель отрицает процесс, из
    # которого печатается). На вид эти две беды одинаковы — строка в таблице
    # просто другого цвета.

    ("маркер снова засчитывается где угодно в командной строке", FARM,
     [("    for tok in argv[1:]:\n"
       "        if _inline_flag(name, tok):\n"
       "            break",
       "    for tok in argv[1:]:\n"
       "        if False:\n"
       "            break")],
     f"{FT}::test_inline_python_code_that_mentions_a_marker_is_not_the_runner"),

    ("PowerShell-однострочник снова считается живым гардианом", FARM,
     [('        return bool(flag) and ("command".startswith(flag)\n'
       '                               or "encodedcommand".startswith(flag))',
       '        return False')],
     f"{FT}::test_a_powershell_one_liner_that_mentions_a_guardian_is_not_the_guardian"),

    ("матчер снова судит по родству и отрицает собственный процесс", FARM,
     [("def _launches(marker: str, row: tuple) -> bool:\n"
       "    return any(marker in tok for tok in _launch_argv(row[1], row[2]))",
       "def _launches(marker: str, row: tuple) -> bool:\n"
       "    if row[0] == os.getpid():\n"
       "        return False\n"
       "    return any(marker in tok for tok in _launch_argv(row[1], row[2]))")],
     f"{FT}::test_the_process_that_serves_the_panel_is_never_reported_missing"),

    ("фильтр «только python» снят с раннеров", FARM,
     [("            if (not py_only or s[1].startswith(\"python\"))", "            if True")],
     f"{FT}::test_a_powershell_runner_is_not_a_python_runner"),

    # Подпись возраста медленной части: беда была НЕ в тексте, а в узле —
    # серверный HTML выглядел безупречно, склейка появлялась только в браузере.
    ("подпись возраста снова печатается вторым узлом рядом", JP,
     [("<span id='slowage'{need_load}>{slow_note}</span>",
       "<span id='slowage'>{slow_note}</span><span id='slowload'></span>"),
      ("  document.getElementById('slowage').textContent=j.note;",
       "  document.getElementById('slowload').textContent=j.note;")],
     f"{T}::test_the_slow_note_replaces_the_server_one_instead_of_standing_next_to_it"),

    # Порог второй колонки — пара к «привязан к устройству». Здесь ломается не
    # диапазон, а СВЯЗЬ двух чисел и замер 707 px на развёрнутом Fold.
    ("минимум колонки поднят обратно, и развёрнутый Fold снова одноколоночный", UI,
     [("minmax(min(320px,100%),1fr)", "minmax(min(360px,100%),1fr)")],
     f"{T}::test_the_state_unfolds_exactly_where_the_second_column_appears"),

    ("порог свёртки состояния разъехался с шириной колонки", JP,
     [("var wide=window.matchMedia('(min-width:690px)');",
       "var wide=window.matchMedia('(min-width:730px)');")],
     f"{T}::test_the_state_unfolds_exactly_where_the_second_column_appears"),

    # ═══════════ ЛЕНТА ПАНЕЛИ, мерж 1 (спека §5) ═══════════════════════════
    #
    # Каждая мутация возвращает КОНКРЕТНЫЙ дефект, найденный на живых данных
    # 14–15.08, а не абстрактную порчу: время без зоны, дебаунс-шум в окне,
    # слурп растущего лога, база не того клиента, кракозябры вместо имени базы.

    # ── Task 1: время строк гардиана ─────────────────────────────────────
    ("время строк гардиана снова считается UTC", FARM,
     [('        return time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))',
       '        import calendar\n'
       '        return calendar.timegm(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))')],
     f"{FD}::test_the_guardian_timestamp_is_read_as_local_time"),

    ("строка без префикса получает выдуманное время", FARM,
     [('    m = _LOG_TS_RE.match(line)\n    if not m:\n        return None',
       '    m = _LOG_TS_RE.match(line)\n    if not m:\n        return _now()')],
     f"{FD}::test_a_line_without_a_timestamp_gets_none_not_a_guess"),

    # ── Task 2: решения против дебаунс-шума ──────────────────────────────
    # Целится в сторожа ПОРЯДКА, а не в цикл по DROP, и это не описка.
    # Проверено (15.08): с пустым списком шума НИ ОДНА настоящая дебаунс-строка
    # в ленту не попадает — именованный `GUARDIAN_DECISIONS` их и так не
    # пропускает («runner check failed» — строчными, `FAILED` в списке —
    # заглавными). Значит первая линия обороны против шума — именованный
    # список, а `GUARDIAN_NOISE` работает ТОЛЬКО на строке, несущей оба
    # признака разом. Такой строки в живом логе нет, поэтому её держит
    # синтетический сторож, и целить мутацию в цикл по DROP значило бы
    # объявить слепым тест, который ничего подобного не обещает.
    ("список дебаунс-шума опустошён — шум перестал перебивать решение", FARM,
     [('GUARDIAN_NOISE = ("debouncing", "runner alive", "heartbeat fresh after")',
       'GUARDIAN_NOISE = ()')],
     f"{FD}::test_noise_outranks_a_decision_word_in_the_same_line"),

    # Парная: «выбросить шум» не имеет права стать «выбросить всё».
    ("фильтр решений выбросил и настоящие события", FARM,
     [('    return any(mark in line for mark in GUARDIAN_DECISIONS)',
       '    return False')],
     f"{FD}::test_decisions_of_the_guardian_reach_the_feed"),

    ("шум перестал перебивать решение", FARM,
     [('    if any(noise in line for noise in GUARDIAN_NOISE):\n        return False\n'
       '    return any(mark in line for mark in GUARDIAN_DECISIONS)',
       '    if any(mark in line for mark in GUARDIAN_DECISIONS):\n        return True\n'
       '    return not any(noise in line for noise in GUARDIAN_NOISE)')],
     f"{FD}::test_noise_outranks_a_decision_word_in_the_same_line"),

    # ── Task 3: окно чтения и кодировка ──────────────────────────────────
    ("seek убран — лог снова слурпается целиком", FARM,
     [("            if size > limit:\n                f.seek(size - limit)",
       "            if False:\n                f.seek(size - limit)")],
     f"{FD}::test_a_log_longer_than_the_window_is_read_from_the_end"),

    ("read(limit) заменён на read() — окно держится на медленности писателя", FARM,
     [("                raw = f.read(limit)", "                raw = f.read()")],
     f"{FD}::test_the_window_stays_a_window_when_the_guardian_writes_mid_read"),

    ("порядок фолбэка перевёрнут — utf-8 читается как cp1251", FARM,
     [('    try:\n        return chunk.decode("utf-8")\n    except UnicodeDecodeError:\n'
       '        return chunk.decode("cp1251", errors="replace")',
       '    return chunk.decode("cp1251", errors="replace")')],
     f"{FD}::test_a_utf8_log_is_read_as_utf8"),

    ("фолбэка на cp1251 больше нет", FARM,
     [('    try:\n        return chunk.decode("utf-8")\n    except UnicodeDecodeError:\n'
       '        return chunk.decode("cp1251", errors="replace")',
       '    return chunk.decode("utf-8", errors="replace")')],
     f"{FD}::test_a_cp1251_log_is_still_readable"),

    ('errors="replace" снят — байт 0x98 роняет страницу', FARM,
     [('        return chunk.decode("cp1251", errors="replace")',
       '        return chunk.decode("cp1251")')],
     f"{FD}::test_a_byte_cp1251_cannot_decode_does_not_kill_the_page"),

    ("BOM больше не снимается — первая строка теряет время", FARM,
     [('    text = text.lstrip("\\ufeff")', '    text = text')],
     f"{FD}::test_a_bom_never_reaches_the_first_line"),

    ("граница окна > заменена на >= — целая первая строка съедена", FARM,
     [("    truncated = size > limit", "    truncated = size >= limit")],
     f"{FD}::test_a_file_exactly_the_size_of_the_window_keeps_its_first_line"),

    ("обрубок первой строки больше не режется", FARM,
     [('        raw = raw.partition(b"\\n")[2]', '        raw = raw')],
     f"{FD}::test_the_first_partial_line_of_the_window_is_dropped"),

    # ── Task 4: лестница источников базы ─────────────────────────────────
    ("база ленты снова прибита литералом", FARM,
     [('    live_db, why = _db_from_live_runner(table)',
       '    return str(ROOT / ".secrets" / "demo.db"), ""\n'
       '    live_db, why = _db_from_live_runner(table)')],
     f"{FD}::test_the_database_follows_the_primary_slug"),

    ("вчерашний лог перебил живой раннер", FARM,
     [('    live_db, why = _db_from_live_runner(table)\n'
       '    if live_db is not None:\n'
       '        return live_db, why              # непусто только при расхождении раннеров\n'
       '    reason = why or "раннер не запущен"\n\n'
       '    log_db, log_ts, log_gap = _db_from_guardian_log()\n'
       '    if log_db is not None:',
       '    live_db, why = _db_from_live_runner(table)\n'
       '    reason = why or "раннер не запущен"\n\n'
       '    log_db, log_ts, log_gap = _db_from_guardian_log()\n'
       '    if log_db is None and live_db is not None:\n'
       '        return live_db, why\n'
       '    if log_db is not None:')],
     f"{FD}::test_the_live_runner_outranks_the_guardian_log"),

    ("раннер, не назвавший базу, снова молча угадывается", FARM,
     [('        return None, "раннер жив, но базу в своём окружении не называет"',
       '        return _db_from_slug("demo"), ""')],
     f"{FD}::test_a_runner_that_names_no_database_says_so_instead_of_guessing_quietly"),

    ("расхождение двух живых раннеров проглочено", FARM,
     [("    if len({db for _, db in answers}) > 1:", "    if False:")],
     f"{FD}::test_two_runners_that_disagree_are_named_an_accident"),

    ("берётся ПЕРВАЯ строка состава вместо последней", FARM,
     [("    for line in reversed(lines):", "    for line in lines:")],
     f"{FD}::test_the_LAST_composition_line_wins_not_the_first"),

    ("CHATTER_DB раннера уступил его же составу", FARM,
     [('    db = (env.get("CHATTER_DB") or "").strip()\n    if db:\n        return _abs_db(db)',
       '    db = (env.get("CHATTER_DB") or "").strip()\n    if False:\n        return _abs_db(db)')],
     f"{FD}::test_the_runners_own_db_variable_outranks_its_personas"),

    ("legacy-умолчание demo снова выдаётся за прочитанный состав", FARM,
     [("    if file_missing and not env_roster:", "    if False:")],
     f"{FD}::test_a_missing_clients_file_admits_it_took_the_legacy_default"),

    ("битый состав молча откатывается на demo.db", FARM,
     [('        return None, (f"склад клиентов не прочитан ({type(exc).__name__}: {exc}) "\n'
       '                      f"— какую базу читать, неизвестно")',
       '        return str(ROOT / ".secrets" / "demo.db"), ""')],
     f"{FD}::test_a_broken_composition_says_so_instead_of_falling_back_silently"),

    ("панель ищет раннера упоминанием, а не запуском (ГРАБЛЯ 1)", FARM,
     [('            if row[1].startswith("python") and _launches(CHATTER_RUNNER, row)]',
       '            if row[1].startswith("python") and any(CHATTER_RUNNER in t for t in row[2])]')],
     f"{FD}::test_a_process_that_only_mentions_the_runner_is_not_asked"),

    # ── Task 5: сборка ленты ─────────────────────────────────────────────
    ("пояснение снова гасит чтение базы (elif вместо if)", FARM,
     [("    if db:\n        try:\n            from app.services.tamapi_metrics import _ro",
       "    elif db:\n        try:\n            from app.services.tamapi_metrics import _ro")],
     f"{FD}::test_a_guessed_database_is_still_READ_not_merely_explained"),

    ("готовая таблица процессов игнорируется", FARM,
     [("    db, db_note = client_db_path(table)", "    db, db_note = client_db_path()")],
     f"{FD}::test_a_ready_process_table_is_not_rebuilt"),

    ("квоты окна нет — источники снова режутся общим срезом", FARM,
     [("    out = fixed + _share_window([client, guard], limit - len(fixed))",
       "    out = fixed + (client + guard)[:max(0, limit - len(fixed))]")],
     f"{FD}::test_a_flood_of_client_events_does_not_starve_the_guardian"),

    # Парная к предыдущей: квота не имеет права стать перекосом в другую сторону.
    ("окно отдано гардиану целиком", FARM,
     [("    out = fixed + _share_window([client, guard], limit - len(fixed))",
       "    out = fixed + (guard + client)[:max(0, limit - len(fixed))]")],
     f"{FD}::test_a_flood_of_guardian_lines_does_not_starve_the_client"),

    ("_share_window снова отдаёт больше бюджета", FARM,
     [("    quota = budget // len(groups)", "    quota = max(1, budget // len(groups))")],
     f"{FD}::test_the_window_never_hands_out_more_than_the_budget"),

    # Сторож, на котором держится САМА квота (решение владельца 15.08: не замер,
    # а тест). Три мутации порознь: снятие, перекос, подмена доли полным окном.
    ("квота снята — поток прячет второй источник", FARM,
     [("    out = fixed + _share_window([client, guard], limit - len(fixed))",
       "    out = fixed + (client + guard)[:max(0, limit - len(fixed))]")],
     f"{FD}::test_the_quota_keeps_the_other_source_visible_under_a_flood"),

    ("квота перекошена в сторону гардиана", FARM,
     [("    out = fixed + _share_window([client, guard], limit - len(fixed))",
       "    out = fixed + (guard + client)[:max(0, limit - len(fixed))]")],
     f"{FD}::test_the_quota_keeps_the_other_source_visible_under_a_flood"),

    ("доля считается от полного окна, а не делится", FARM,
     [("    quota = budget // len(groups)", "    quota = budget")],
     f"{FD}::test_the_quota_keeps_the_other_source_visible_under_a_flood"),

    ("группа гардиана приезжает в дележ неотсортированной", FARM,
     [("    guard.sort(key=_newest_first)", "    pass")],
     f"{FD}::test_the_window_takes_the_NEWEST_decisions_of_the_guardian_not_the_first"),

    ("суп из подстрок вернулся вместо is_decision", FARM,
     [("    kept = [ln for ln in lines if is_decision(ln)]",
       '    kept = [ln for ln in lines\n'
       '            if "DOWN" in ln or "launched" in ln or "failed" in ln]')],
     f"{FD}::test_the_debounce_noise_never_reaches_the_feed_ITSELF"),

    ("граница видимости снова подписана самым старым временем", FARM,
     [('                      "detail": f"лог длиннее окна: видно с {seen_from}",\n'
       '                      "ts": None})',
       '                      "detail": f"лог длиннее окна: видно с {seen_from}",\n'
       '                      "ts": oldest})')],
     f"{FD}::test_the_visibility_border_reaches_the_top_of_a_NON_EMPTY_feed"),

    ("кривое время одной строки снова валит всю ленту", FARM,
     [('    ts = _as_ts(e.get("ts"))\n    return (ts is not None, -(ts or 0.0))',
       '    ts = e.get("ts")\n    return (ts is not None, -(ts or 0.0))')],
     f"{FD}::test_a_broken_timestamp_is_not_a_number_for_the_sort_key"),

    ("отрицательный limit снова уходит в SQL без предела", FARM,
     [("    limit = max(0, limit)", "    limit = limit")],
     f"{FD}::test_a_negative_limit_never_becomes_an_unbounded_sql_query"),

    ("разметка снова режет ленту вторым числом", JP,
     [("        for e in events)", "        for e in events[:25])")],
     f"{FD}::test_the_renderer_draws_everything_it_was_given"),

    ("предел детали в разметке снова свой", JP,
     [("{esc((e['detail'] or '')[:F.FEED_DETAIL_LIMIT])}",
       "{esc((e['detail'] or '')[:110])}")],
     f"{FD}::test_the_detail_limit_is_a_single_number_from_source_to_screen"),

    ("колонка источника снова рвётся посреди слова", JP,
     [("<td data-l='Источник' class='sub nobreak'>",
       "<td data-l='Источник' class='sub'>")],
     f"{FD}::test_short_columns_of_the_feed_are_not_broken_mid_word"),

    # Парная к предыдущей: класс в разметке без правила в CSS — та же порча,
    # только с другого конца, и выглядит она как «всё на месте».
    ("правило nobreak исчезло из CSS", UI,
     [(".nobreak{overflow-wrap:normal;white-space:nowrap}",
       ".nobreak{overflow-wrap:anywhere}")],
     f"{FD}::test_short_columns_of_the_feed_are_not_broken_mid_word"),

    ("машинное имя события снова рвётся где попало", JP,
     [("{esc(e['kind']).replace('_', '_<wbr>')}", "{esc(e['kind'])}")],
     f"{FD}::test_a_machine_event_name_breaks_at_the_underscore_not_mid_word"),

    # ── Task 6: кодировка лога у писателя ────────────────────────────────
    ("гардиан снова пишет лог в системной кодировке", PS1,
     [("Add-Content -LiteralPath $gOut -Value $line -Encoding utf8",
       "Add-Content -LiteralPath $gOut -Value $line")],
     f"{FD}::test_the_guardian_writes_its_log_in_utf8_not_in_the_system_codepage"),

    # ── Заход 2: журнал событий (§4 спеки 2026-08-14) ─────────────────────
    #
    # Читатель и писатель — РАЗНЫЕ файлы с продублированным форматом (сторож
    # stdlib-only, импортировать app/ он не имеет права). Поэтому мутации по
    # читателю живут здесь, а по писателю — в mutate_ops_watchdog.py.
    ("молчащий писатель снова читается как тишина фермы", FARM,
     [("    if beat_age is None or beat_age > JOURNAL_BEAT_FRESH:",
       "    if False:")],
     f"{JV}::test_a_stale_marker_means_the_writer_is_silent_not_the_farm"),

    ("тревога о писателе стала вечной", FARM,
     [("    if beat_age is None or beat_age > JOURNAL_BEAT_FRESH:",
       "    if True:")],
     f"{JV}::test_a_fresh_marker_with_an_empty_journal_means_real_silence"),

    ("порог свежести маркера поднят до суток", FARM,
     [("JOURNAL_BEAT_FRESH = 180.0", "JOURNAL_BEAT_FRESH = 86400.0")],
     f"{JV}::test_a_stale_marker_means_the_writer_is_silent_not_the_farm"),

    ("маркер из будущего снова принят за свежий", FARM,
     [("        beat_age = abs(now - (ROOT /", "        beat_age = (now - (ROOT /")],
     f"{JV}::test_a_marker_from_the_future_is_not_read_as_fresh"),

    ("окно экрана расширено до месяца", FARM,
     [("JOURNAL_WINDOW_S = 72 * 3600", "JOURNAL_WINDOW_S = 30 * 86400")],
     f"{JV}::test_records_older_than_the_window_are_not_shown"),

    # Читатель обязан быть НЕ СЛАБЕЕ писателя: обе мутации ниже воспроизводят
    # молчаливую потерю события, а не падение.
    ("читатель снова режет журнал по U+2028", FARM,
     [('    for line in text.split("\\n"):', "    for line in text.splitlines():")],
     f"{JV}::test_a_line_separator_inside_detail_does_not_split_the_record"),

    ("BOM снова съедает самую старую запись", FARM,
     [('        line = line.strip(" \\t\\r\\ufeff")', "        line = line.strip()")],
     f"{JV}::test_a_bom_does_not_eat_the_oldest_record"),

    ("±inf принят за время — запись бессмертна, сортировка сломана", FARM,
     [('    if ts != ts or ts in (float("inf"), float("-inf")):', "    if ts != ts:")],
     f"{JV}::test_unreadable_time_does_not_take_down_the_whole_page"),

    ("большое ЦЕЛОЕ время роняет ВЕСЬ первый экран фермы", FARM,
     [("    except (TypeError, ValueError, OverflowError):",
       "    except (TypeError, ValueError):")],
     f"{JV}::test_unreadable_time_does_not_take_down_the_whole_page"),

    ("не-словарь принят за запись — `.get` на числе роняет страницу", FARM,
     [("    if not isinstance(rec, dict):\n        return None",
       "    if False:\n        return None")],
     f"{JV}::test_junk_that_is_not_an_object_is_not_a_record"),

    # ── схлопывание «подавлено → исход» (§4.6) ───────────────────────────
    ("схлопывание проглотило соседнюю пробу", FARM,
     [('            if j in consumed or nxt.get("check") != rec.get("check"):',
       "            if j in consumed:")],
     f"{JV}::test_other_checks_are_not_swallowed_by_the_collapse"),

    ("подавленное и подтверждение снова две строки", FARM,
     [('        if not isinstance(rec, dict) or rec.get("kind") != "suppressed":\n'
       "            out.append(rec)\n            continue",
       "        out.append(rec)\n        continue")],
     f"{JV}::test_suppressed_then_down_is_one_line_with_the_confirmation_delay"),

    ("исход схвачен ЧЕРЕЗ промежуточную запись — два инцидента склеены", FARM,
     [('                row["after_s"] = None if (a is None or b is None) else b - a\n'
       "            break",
       '                row["after_s"] = None if (a is None or b is None) else b - a\n'
       "            continue")],
     f"{JV}::test_only_the_first_outcome_is_taken_not_any_later_one"),

    ("схлопывание правит запись снапшота на месте", FARM,
     [("        row = dict(rec)", "        row = rec")],
     f"{JV}::test_the_incoming_records_are_not_mutated"),

    # ── разметка блока «Что изменилось» (§4.1-§4.3) ──────────────────────
    ("старые сутки снова выкладываются списком", JP,
     [('            parts.append(_group(f"{_JOURNAL_TITLES[key]}: {len(buckets[key])}",\n'
       "                                inner, len(buckets[key])))",
       '            parts.append(f"<h2>{_JOURNAL_TITLES[key]}</h2>{inner}")')],
     f"{JV}::test_todays_events_are_open_and_older_days_are_counted"),

    ("сутки снова 24-часовые куски вместо календарных", JP,
     [("        return (date.fromtimestamp(now) - date.fromtimestamp(t)).days",
       "        return int((now - t) // 86400)")],
     f"{JV}::test_a_late_night_event_belongs_to_yesterday_not_to_today"),

    ("молчание писателя снова выглядит списком, которому верят", JP,
     [("    if note:\n        # Молчание писателя не имеет права выглядеть тишиной фермы.",
       "    if False:\n        # Молчание писателя не имеет права выглядеть тишиной фермы.")],
     f"{JV}::test_a_silent_writer_replaces_the_list_with_words"),

    ("пустой журнал молчит вместо утверждения «переходов не было»", JP,
     [('    if not records:\n        return ("<h2>Что изменилось</h2><div class=\'card\'>"',
       '    if False:\n        return ("<h2>Что изменилось</h2><div class=\'card\'>"')],
     f"{JV}::test_an_empty_journal_says_it_out_loud"),

    ("разделитель рестарта снова читается подписью кнопки", JP,
     [('        if r.get("kind") == _JOURNAL_RESTART:', "        if False:")],
     f"{JV}::test_the_restart_divider_is_not_a_button_label"),

    ("деталь записи попадает на страницу сырой", JP,
     [("{esc((r.get('detail') or '')[:110])}", "{(r.get('detail') or '')[:110]}")],
     f"{JV}::test_a_detail_with_markup_cannot_reach_the_page_raw"),

    # ── потолок строк суток (найден замером бюджета 15.08) ───────────────
    ("шторм рестартов снова заливает первый экран", JP,
     [("JOURNAL_ROWS_PER_DAY = 50", "JOURNAL_ROWS_PER_DAY = 10**9")],
     f"{JV}::test_a_storm_of_events_does_not_flood_the_first_screen"),

    ("хвост суток срезан МОЛЧА", JP,
     [("        if hidden > 0:", "        if False:")],
     f"{JV}::test_a_storm_of_events_does_not_flood_the_first_screen"),

    ("якорь рестарта срезан вместе с хвостом", JP,
     [("        shown = events[:JOURNAL_ROWS_PER_DAY] + anchors",
       "        shown = items[:JOURNAL_ROWS_PER_DAY]")],
     f"{JV}::test_the_restart_anchor_survives_the_cap"),

    # ── типографика журнала (найдено СКРИНШОТОМ приёмки на 707 px) ────────
    ("имя пробы снова рвётся посреди слова", JP,
     [("{esc(r.get('check', '—')).replace('_', '_<wbr>')}", "{esc(r.get('check', '—'))}")],
     f"{JV}::test_machine_probe_names_break_at_the_underscore_not_mid_word"),

    # Парная: `<wbr>` без `wordsafe` не спасает имя БЕЗ подчёркиваний
    # (`worktree`), а именно оно и рвалось на скриншоте.
    ("имя пробы без подчёркиваний снова рвётся где попало", JP,
     [("f\"<tr><td class='wordsafe'><b>\"", "f\"<tr><td><b>\"")],
     f"{JV}::test_machine_probe_names_break_at_the_underscore_not_mid_word"),

    ("фраза вида записи снова рвётся посреди слова", JP,
     [("<div class='sub wordsafe'>", "<div class='sub'>")],
     f"{JV}::test_machine_probe_names_break_at_the_underscore_not_mid_word"),

    ("возраст записи снова рвётся посреди слова", JP,
     # Фрагмент с ХВОСТОМ: голое `data-l='Когда' class='sub nobreak'` есть и в
     # ленте, и в журнале, а гейт меняет ПЕРВОЕ вхождение — мутация ушла бы в
     # ленту, и её ловил бы чужой сторож, а этот остался бы слепым.
     [("<td data-l='Когда' class='sub nobreak'>{esc(_ago(F.journal_ts(r), now))}",
       "<td data-l='Когда' class='sub'>{esc(_ago(F.journal_ts(r), now))}")],
     f"{JV}::test_machine_probe_names_break_at_the_underscore_not_mid_word"),

    # Парная: класс в разметке без правила в CSS — та же порча с другого конца.
    ("правило wordsafe исчезло из CSS", UI,
     [(".wordsafe{overflow-wrap:normal}", ".wordsafe{overflow-wrap:anywhere}")],
     f"{JV}::test_the_wordsafe_rule_exists_in_the_css"),
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
