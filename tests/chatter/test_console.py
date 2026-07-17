"""Чистый парсер команд пульта. Ноль Telethon."""
from __future__ import annotations

import re

from chatter.core.console import (
    CONSOLE_STRINGS,
    Command,
    PauseView,
    console_text,
    contact_link,
    display_name,
    escape_html,
    format_status,
    parse_command,
    safe_snippet,
)

HOUR = 3600.0


def _view(**kw) -> PauseView:
    base = dict(title="Иван Петров", link="t.me/ivan", since_ts=0.0,
                source="human_takeover", detail="Здравствуйте, я сам перезвоню",
                msg_id=4821, resume_eta_ts=3 * HOUR)
    base.update(kw)
    return PauseView(**base)


def test_global_commands():
    assert parse_command("/status") == Command(name="status")
    assert parse_command("/stop") == Command(name="stop")
    assert parse_command("/start") == Command(name="start")


def test_case_and_whitespace_tolerated():
    assert parse_command("  /STATUS  ") == Command(name="status")


def test_pause_durations():
    assert parse_command("/pause 1h") == Command(name="pause", duration_seconds=3600.0)
    assert parse_command("/pause 30m") == Command(name="pause", duration_seconds=1800.0)
    assert parse_command("/pause") == Command(name="pause", duration_seconds=None)


def test_pause_with_explicit_target():
    assert parse_command("/pause 1h t.me/ivan") == Command(
        name="pause", duration_seconds=3600.0, target="t.me/ivan")
    assert parse_command("/resume 237616472") == Command(name="resume", target="237616472")


def test_pause_with_target_only_no_duration():
    # «Заглуши вот этот диалог насовсем» — реальный сценарий, отдельный от
    # /pause <длительность> <ссылка>. Ссылка не должна приниматься за кривую
    # длительность и падать в error.
    assert parse_command("/pause t.me/ivan") == Command(
        name="pause", duration_seconds=None, target="t.me/ivan")


def test_pause_with_numeric_id_target_only_no_duration():
    # Числовой id тоже похож на «аргумент без буквы h/m» — убедиться, что он
    # уходит в target, а не ошибочно трактуется как кривая длительность.
    assert parse_command("/pause 237616472") == Command(
        name="pause", duration_seconds=None, target="237616472")


def test_not_a_command():
    assert parse_command("просто текст") is None
    assert parse_command("") is None
    assert parse_command("/unknown") is None


def test_bad_duration_is_reported_not_silently_ignored():
    # Молча проглотить «/pause 1час» = владелец думает, что поставил паузу.
    cmd = parse_command("/pause 1час")
    assert cmd == Command(name="pause", error="не понял длительность: '1час' (примеры: 1h, 30m)")


def test_status_answers_why_is_she_silent_with_a_reason_per_dialog():
    out = format_status(kill_switch=False, pauses=[_view()],
                        counters={"takeover": 2, "unattributed_pause": 0, "unknown_outgoing": 0},
                        autoresume_beat_age=12.0, autoresume_interval=60.0,
                        now=1 * HOUR, window_hours=24)
    assert "Иван Петров" in out
    assert "вы вмешались" in out
    assert "Здравствуйте, я сам перезвоню" in out    # ЧТО именно вызвало паузу
    assert "4821" in out                              # атрибуция по id
    assert "РАБОТАЕТ" in out


def test_status_with_multiple_pauses_keeps_each_dialog_distinct():
    # Спека §11/§4: несколько пауз одновременно — с РАЗНЫМИ причинами и ETA.
    # Цикл по pauses, порядок и границы между блоками ничем не проверялись.
    maria = _view(title="Мария К.", link="t.me/maria", since_ts=1 * HOUR,
                  source="command", detail=None, msg_id=None,
                  resume_eta_ts=5 * HOUR)
    out = format_status(kill_switch=False, pauses=[_view(), maria],
                        counters={"takeover": 2, "unattributed_pause": 0, "unknown_outgoing": 0},
                        autoresume_beat_age=12.0, autoresume_interval=60.0,
                        now=2 * HOUR, window_hours=24)
    assert "Заглушено диалогов: 2" in out
    assert "Иван Петров" in out
    assert "Мария К." in out
    assert "вы вмешались" in out
    assert "команда /pause" in out

    # Блоки не слиплись: причина Ивана («вы вмешались», с деталью/msg_id)
    # не приклеилась к строке Марии, у которой своя причина без детали.
    lines = out.splitlines()
    ivan_reason_idx = next(i for i, l in enumerate(lines) if "вы вмешались" in l)
    maria_reason_idx = next(i for i, l in enumerate(lines) if "команда /pause" in l)
    assert "Мария" not in lines[ivan_reason_idx]
    assert "Иван" not in lines[maria_reason_idx]


def test_status_indefinite_pause_says_so_and_omits_auto_resume_line():
    # §8: /pause без длительности бессрочен — is_muted/should_auto_resume это
    # уже гарантируют в коде (pause.py), но владелец видит только ЭКРАН.
    # Без этой строки гарантия существует для кода, не для человека.
    out = format_status(kill_switch=False, pauses=[_view(resume_eta_ts=None)],
                        counters={"takeover": 1, "unattributed_pause": 0, "unknown_outgoing": 0},
                        autoresume_beat_age=12.0, autoresume_interval=60.0,
                        now=1 * HOUR, window_hours=24)
    assert "авто-возврата нет" in out
    assert "авто-возврат через" not in out


def test_status_multiline_detail_does_not_forge_an_extra_status_line():
    # Владелец пишет клиенту через Shift+Enter — это ОБЫЧНЫЙ сценарий, не
    # эксплойт. Если \n просочится в lines.append(...) как есть, join('\n')
    # печатает второй "физический" абзац без отступа "   причина: " —
    # неотличимый от новой строки статуса (может даже начаться с "• ").
    # Однострочный и многострочный detail ОДИНАКОВОЙ смысловой длины обязаны
    # давать ОДИНАКОВОЕ число строк в выводе.
    single = format_status(
        kill_switch=False,
        pauses=[_view(detail="Уже беру трубку, отвечу через 10 минут")],
        counters={}, autoresume_beat_age=12.0, autoresume_interval=60.0,
        now=1 * HOUR, window_hours=24)
    multi = format_status(
        kill_switch=False,
        pauses=[_view(detail="Уже беру трубку,\nотвечу через 10 минут")],
        counters={}, autoresume_beat_age=12.0, autoresume_interval=60.0,
        now=1 * HOUR, window_hours=24)
    assert len(multi.splitlines()) == len(single.splitlines())
    # Вторая половина текста не должна всплыть в начале отдельной строки —
    # это и есть подделанная строка статуса.
    assert not any(line.startswith("отвечу через 10 минут") for line in multi.splitlines())


def test_status_multiline_title_does_not_forge_an_extra_status_line():
    out = format_status(
        kill_switch=False,
        pauses=[_view(title="Иван\n• Мария", detail=None)],
        counters={}, autoresume_beat_age=12.0, autoresume_interval=60.0,
        now=1 * HOUR, window_hours=24)
    baseline = format_status(
        kill_switch=False,
        pauses=[_view(title="Иван — Мария", detail=None)],
        counters={}, autoresume_beat_age=12.0, autoresume_interval=60.0,
        now=1 * HOUR, window_hours=24)
    assert len(out.splitlines()) == len(baseline.splitlines())
    assert not any(line.strip().startswith("• Мария") for line in out.splitlines())


def test_status_shows_the_kill_switch_first():
    out = format_status(kill_switch=True, pauses=[], counters={},
                        autoresume_beat_age=5.0, autoresume_interval=60.0,
                        now=0.0, window_hours=24)
    assert "ЗАГЛУШЕНА" in out
    assert "/start" in out          # как расколдовать — прямо в ответе


def test_status_flags_a_dead_autoresume_task_instead_of_staying_quiet():
    # Мёртвый таймер выглядит РОВНО как «пауз к возврату нет»: тихо и
    # правдоподобно. Единственная разница — возраст heartbeat.
    out = format_status(kill_switch=False, pauses=[], counters={},
                        autoresume_beat_age=47 * 60.0, autoresume_interval=60.0,
                        now=0.0, window_hours=24)
    assert "⚠️" in out


def test_status_without_any_pause_says_so_plainly():
    out = format_status(kill_switch=False, pauses=[], counters={},
                        autoresume_beat_age=3.0, autoresume_interval=60.0,
                        now=0.0, window_hours=24)
    assert "Заглушено диалогов: 0" in out



# ---------------------------------------------------------------------------
# Задача 1 под-арки 3A-UX: человеческие имена вместо голых id, i18n, escaping.
# ---------------------------------------------------------------------------

def test_display_name_full_name():
    assert display_name(first_name="Даниил", last_name="Лапин") == "Даниил Лапин"


def test_display_name_first_name_only():
    assert display_name(first_name="Вася") == "Вася"


def test_display_name_last_name_only():
    # first_name бывает пуст (Telegram это допускает) — фамилия одна тоже имя.
    assert display_name(last_name="Иванов") == "Иванов"


def test_display_name_title_for_channels_and_groups():
    assert display_name(title="Клиенты салона") == "Клиенты салона"


def test_display_name_username_only():
    assert display_name(username="lapin") == "@lapin"


def test_display_name_nothing_but_id():
    # Голый id — признак того, что о человеке НИЧЕГО не известно, а не норма.
    assert display_name(user_id=237616472) == "237616472"


def test_display_name_combines_name_and_username():
    assert display_name(first_name="Даниил", last_name="Лапин", username="lapin") == \
        "Даниил Лапин (@lapin)"


def test_display_name_never_bare_id_when_name_or_username_known():
    # Главный тест под критерий приёмки спеки: если известно хоть что-то --
    # имя, фамилия или юзернейм -- голый числовой id не должен всплыть.
    uid = 237616472
    assert str(uid) not in display_name(first_name="Иван", user_id=uid)
    assert str(uid) not in display_name(username="ivan", user_id=uid)
    assert str(uid) not in display_name(title="Группа", user_id=uid)


def test_display_name_escapes_html_special_chars():
    # first_name -- ПОЛЬЗОВАТЕЛЬСКИЙ текст (владелец профиля пишет что хочет
    # себе в имя). Если не экранировать, "<script>" в имени сломает
    # HTML-разметку карточки/статуса ровно как detail с тегами (спека §7).
    out = display_name(first_name="<script>")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_contact_link_prefers_username():
    assert contact_link(username="lapin", user_id=237616472) == "t.me/lapin"


def test_contact_link_falls_back_to_id_link():
    assert contact_link(user_id=237616472) == "tg://user?id=237616472"


def test_escape_html_covers_all_five_special_chars():
    out = escape_html("<b>a & b \"c\" 'd'</b>")
    assert "<" not in out.replace("&lt;", "").replace("&gt;", "")
    assert "&lt;b&gt;" in out
    assert "&amp;" in out
    assert "&quot;" in out
    # html.escape(quote=True) кодирует апостроф числовой сущностью &#x27;.
    assert "&#x27;" in out


def test_safe_snippet_escapes_angle_brackets_and_ampersand():
    assert safe_snippet("<b>hi</b>") == "&lt;b&gt;hi&lt;/b&gt;"


def test_safe_snippet_escapes_quotes():
    out = safe_snippet('she said "hi"')
    assert "&quot;" in out
    assert '"' not in out


def test_safe_snippet_collapses_newline_and_escapes_together():
    # Комбинация из спеки: перевод строки (подделка "физической" строки
    # статуса, найдено в 3A) И спецсимвол (подделка HTML-разметки) в ОДНОМ
    # detail. Обе защиты обязаны сработать одновременно, не по очереди.
    out = safe_snippet("line1 <b>&\nline2")
    assert "\n" not in out
    assert "<b>" not in out
    assert "&lt;b&gt;" in out


def test_safe_snippet_truncation_never_cuts_an_entity_in_half():
    # Если бы порядок был "экранировать -> обрезать", символ "&" ровно на
    # границе среза расширился бы в "&amp;" (5 симв.), а обрезка по [:40]
    # разрубила бы её на "&am" -- Telegram отказался бы парсить HTML
    # целиком. Экранирование ПОСЛЕ обрезки исключает это по построению:
    # что бы ни осталось после среза сырого текста, escape() всегда выдаёт
    # ЦЕЛУЮ сущность для каждого спецсимвола в остатке.
    text = "a" * 39 + "&" + "amp;rest of a very long trailing string"
    out = safe_snippet(text, limit=40)
    assert "&amp;" in out
    # Ни одной "оборванной" сущности: любой "&" в результате -- начало
    # ПОЛНОЙ известной сущности, не хвост "&am"/"&l"/"&quo" и т.п.
    assert re.search(r"&(?!amp;|lt;|gt;|quot;|#x27;)", out) is None


def test_safe_snippet_short_text_unaffected():
    assert safe_snippet("hello") == "hello"


def test_console_text_ru_default():
    assert console_text("list_is_stale", "ru") == "список устарел, набери /status"


def test_console_text_en():
    assert console_text("list_is_stale", "en") == "list is stale, run /status"


def test_console_text_uk():
    assert console_text("list_is_stale", "uk") == "список застарів, наберіть /status"


def test_console_text_unknown_language_falls_back_to_ru():
    # Тот же паттерн, что disclosure.honest_disclosure: неизвестный язык -> ru.
    assert console_text("list_is_stale", "de") == console_text("list_is_stale", "ru")


def test_console_text_formats_kwargs():
    assert console_text("resume_hint", "ru", n=3) == "→ /resume 3"


def test_console_strings_no_key_lost_in_any_language():
    # Тест-страж: набор ключей во всех трёх словарях ОБЯЗАН совпадать -- иначе
    # английский клиент получит дыру в интерфейсе (KeyError или голый ключ)
    # там, где русский владелец её никогда не увидит.
    ru_keys = set(CONSOLE_STRINGS["ru"])
    en_keys = set(CONSOLE_STRINGS["en"])
    uk_keys = set(CONSOLE_STRINGS["uk"])
    assert ru_keys == en_keys == uk_keys
    assert len(ru_keys) > 0


def test_status_flags_autoresume_that_never_ran_even_once():
    # Раннер только что стартовал или задача авто-возврата умерла ДО первого
    # прогона: beat_age=None неотличим от «пауз к возврату нет», если не
    # проверить его отдельно от «прогон был давно» (DEV-18 — не молчать).
    out = format_status(kill_switch=False, pauses=[], counters={},
                        autoresume_beat_age=None, autoresume_interval=60.0,
                        now=0.0, window_hours=24)
    assert "⚠️" in out
