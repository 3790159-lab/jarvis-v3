# -*- coding: utf-8 -*-
"""Этап 2 — генератор задач v0 (/suggest_tasks): pure signal/prompt/parse module.

$0, no network, no file IO — every collector here takes already-read data
(baseline dict, doc text, log lines) so the whole pipeline is testable on
plain Python values. The real file reads + the billable LLM call live in the
control-file wiring layer (tested separately, mocked).
"""
from datetime import date

from app.services.devtask import suggest as sug


# ── regress_signal ──────────────────────────────────────────────────────────
def test_regress_signal_formats_baseline():
    text = sug.regress_signal({"failed": 33, "passed": 3966, "errors": 0})
    assert "33" in text and "3966" in text and "0" in text


def test_regress_signal_handles_missing_baseline():
    text = sug.regress_signal(None)
    assert "baseline" in text.lower()


# ── backlog_signal ───────────────────────────────────────────────────────────
_MASTER_PLAN_SAMPLE = """
### Этап 1 — Регресс-гейт

1. `[x]` **Single-instance guard, слои B+C** — уже готово, закрыто.
2. `[ ]` **Ancestor-check влитых веток в конвейере** — при merge-base
   автоматически закрывать карточку.
3. `[ ]` **Изоляция тестовых уведомлений от прод-чата** — регресс/тесты не
   должны слать в реальный чат админа.
4. `[x]` **RAM-guard + батчи регресса** — закрыт живым прогоном.
5. `[ ]` **Таргет-режим регресса** — обход полного регресса по диффу.
"""


def test_backlog_signal_extracts_only_unchecked_items_in_order():
    items = sug.backlog_signal(_MASTER_PLAN_SAMPLE)
    assert items == [
        "Ancestor-check влитых веток в конвейере",
        "Изоляция тестовых уведомлений от прод-чата",
        "Таргет-режим регресса",
    ]


def test_backlog_signal_ignores_checked_items():
    items = sug.backlog_signal(_MASTER_PLAN_SAMPLE)
    assert not any("Single-instance" in it for it in items)
    assert not any("RAM-guard" in it for it in items)


def test_backlog_signal_respects_limit():
    items = sug.backlog_signal(_MASTER_PLAN_SAMPLE, limit=2)
    assert len(items) == 2


def test_backlog_signal_empty_text_returns_empty_list():
    assert sug.backlog_signal("") == []
    assert sug.backlog_signal(None) == []


# ── recent_error_lines ───────────────────────────────────────────────────────
_LOG_SAMPLE = [
    "2026-07-07 10:00:00 | ERROR    | app.foo | too old, outside window",
    "2026-07-09 11:00:00 | INFO     | app.foo | not an error level",
    "2026-07-09 12:00:00 | ERROR    | app.bar | recent real error",
    "2026-07-10 09:00:00 | CRITICAL | app.baz | recent critical",
    "2026-07-10 09:05:00 | WARNING  | app.baz | warning is not in default levels",
    "not a log line at all — noise",
]


def test_recent_error_lines_filters_by_window_and_level():
    out = sug.recent_error_lines(_LOG_SAMPLE, today=date(2026, 7, 10), since_days=2)
    assert any("recent real error" in ln for ln in out)
    assert any("recent critical" in ln for ln in out)
    assert not any("too old" in ln for ln in out)          # outside the 2-day window
    assert not any("not an error level" in ln for ln in out)  # INFO excluded
    assert not any("warning is not" in ln for ln in out)    # WARNING not in default levels


def test_recent_error_lines_ignores_unparseable_lines():
    out = sug.recent_error_lines(_LOG_SAMPLE, today=date(2026, 7, 10), since_days=2)
    assert not any("noise" in ln for ln in out)


def test_recent_error_lines_respects_limit():
    lines = [f"2026-07-10 09:00:0{i} | ERROR    | app.x | err {i}" for i in range(5)]
    out = sug.recent_error_lines(lines, today=date(2026, 7, 10), since_days=1, limit=3)
    assert len(out) == 3


def test_recent_error_lines_empty_input():
    assert sug.recent_error_lines([], today=date(2026, 7, 10)) == []


# ── done_signal (v0.2: "уже реализовано" — не предлагать снова) ─────────────
def test_done_signal_extracts_only_checked_items_in_order():
    items = sug.done_signal(_MASTER_PLAN_SAMPLE)
    assert items == [
        "Single-instance guard, слои B+C",
        "RAM-guard + батчи регресса",
    ]


def test_done_signal_ignores_unchecked_items():
    items = sug.done_signal(_MASTER_PLAN_SAMPLE)
    assert not any("Ancestor-check" in it for it in items)
    assert not any("Таргет-режим" in it for it in items)


def test_done_signal_respects_limit():
    items = sug.done_signal(_MASTER_PLAN_SAMPLE, limit=1)
    assert len(items) == 1


def test_done_signal_empty_text_returns_empty_list():
    assert sug.done_signal("") == []
    assert sug.done_signal(None) == []


# ── merged_task_signal (v0.3: "уже реализовано" from merged dev_task cards) ─
def test_merged_task_signal_extracts_title_and_date():
    merged = [{"desc": "Log-контаминация: изолировать test-pollution", "merged_at": "2026-07-10T22:31:45.612522"}]
    items = sug.merged_task_signal(merged)
    assert items == [{"title": "Log-контаминация: изолировать test-pollution", "date": "2026-07-10"}]


def test_merged_task_signal_uses_first_line_of_desc_as_title():
    merged = [{"desc": "Полная спека\nвторая строка не входит", "merged_at": "2026-07-10T22:00:00"}]
    items = sug.merged_task_signal(merged)
    assert items[0]["title"] == "Полная спека"


def test_merged_task_signal_truncates_long_titles():
    merged = [{"desc": "x" * 200, "merged_at": "2026-07-10T00:00:00"}]
    items = sug.merged_task_signal(merged)
    assert len(items[0]["title"]) <= 120
    assert items[0]["title"].endswith("...")


def test_merged_task_signal_honest_when_date_missing():
    merged = [{"desc": "no merge date on this card", "merged_at": None}]
    items = sug.merged_task_signal(merged)
    assert items[0]["date"] == "дата неизвестна"


def test_merged_task_signal_respects_limit():
    merged = [{"desc": f"task {i}", "merged_at": "2026-07-10T00:00:00"} for i in range(5)]
    items = sug.merged_task_signal(merged, limit=2)
    assert len(items) == 2


def test_merged_task_signal_empty_input():
    assert sug.merged_task_signal(None) == []
    assert sug.merged_task_signal([]) == []


def test_merged_task_signal_skips_blank_desc():
    items = sug.merged_task_signal([{"desc": "  ", "merged_at": "2026-07-10T00:00:00"}])
    assert items == []


# ── done_signal_records (structured Сигнал 4: MASTER-PLAN + merged cards) ───
def test_done_signal_records_combines_master_plan_and_merged():
    merged = [{"desc": "RAM-guard fix", "merged_at": "2026-07-10T00:00:00"}]
    records = sug.done_signal_records(_MASTER_PLAN_SAMPLE, merged_tasks=merged)
    titles = [r["title"] for r in records]
    assert "Single-instance guard, слои B+C" in titles
    assert "RAM-guard fix" in titles


def test_done_signal_records_master_plan_items_have_no_date():
    records = sug.done_signal_records(_MASTER_PLAN_SAMPLE)
    assert all(r["date"] is None for r in records)


def test_done_signal_records_merged_items_carry_date():
    merged = [{"desc": "shipped fix", "merged_at": "2026-07-11T00:00:00"}]
    records = sug.done_signal_records(None, merged_tasks=merged)
    assert records == [{"title": "shipped fix", "date": "2026-07-11"}]


# ── build_signals ─────────────────────────────────────────────────────────────
def test_build_signals_combines_all_three():
    signals = sug.build_signals(
        baseline={"failed": 5, "passed": 100, "errors": 0},
        master_plan_text=_MASTER_PLAN_SAMPLE,
        log_lines=_LOG_SAMPLE,
        today=date(2026, 7, 10),
        since_days=2,
    )
    assert "5" in signals["regress"]
    assert len(signals["backlog"]) == 3
    assert any("recent critical" in ln for ln in signals["errors"])


def test_build_signals_includes_done_signal():
    signals = sug.build_signals(
        baseline=None, master_plan_text=_MASTER_PLAN_SAMPLE, log_lines=[],
        today=date(2026, 7, 10),
    )
    assert signals["done"] == [
        "Single-instance guard, слои B+C",
        "RAM-guard + батчи регресса",
    ]


# ── build_signals: merged dev_task cards feed Сигнал 4 too (v0.3) ───────────
def test_build_signals_done_includes_merged_tasks_with_date():
    signals = sug.build_signals(
        baseline=None, master_plan_text=None, log_lines=[], today=date(2026, 7, 10),
        merged_tasks=[{"desc": "Log-контаминация фикс", "merged_at": "2026-07-09T00:00:00"}],
    )
    assert "Log-контаминация фикс (2026-07-09)" in signals["done"]


def test_build_signals_exposes_done_records_for_post_parse_matching():
    signals = sug.build_signals(
        baseline=None, master_plan_text=_MASTER_PLAN_SAMPLE, log_lines=[], today=date(2026, 7, 10),
        merged_tasks=[{"desc": "Merged fix", "merged_at": "2026-07-09T00:00:00"}],
    )
    titles = [r["title"] for r in signals["done_records"]]
    assert "Single-instance guard, слои B+C" in titles
    assert "Merged fix" in titles


# ── build_prompt ───────────────────────────────────────────────────────────────
def test_build_prompt_returns_system_and_user_message():
    signals = {"regress": "33 failed", "backlog": ["item A"], "errors": ["err B"], "done": []}
    system, messages = sug.build_prompt(signals)
    assert "JSON" in system
    assert isinstance(messages, list) and len(messages) == 1
    assert messages[0]["role"] == "user"
    assert "item A" in messages[0]["content"]
    assert "err B" in messages[0]["content"]
    assert "33 failed" in messages[0]["content"]


def test_build_prompt_handles_empty_signals():
    system, messages = sug.build_prompt(
        {"regress": "нет данных", "backlog": [], "errors": [], "done": []}
    )
    assert messages[0]["content"]  # no crash on empty lists


# ── build_prompt: "уже реализовано" (v0.2 — don't re-suggest done work) ─────
def test_build_prompt_includes_done_section_in_user_message():
    signals = {"regress": "x", "backlog": [], "errors": [],
               "done": ["Таргет-режим регресса"]}
    _, messages = sug.build_prompt(signals)
    assert "Таргет-режим регресса" in messages[0]["content"]
    assert "уже реализовано" in messages[0]["content"].lower()


def test_build_prompt_done_section_empty_is_honest_not_omitted():
    signals = {"regress": "x", "backlog": [], "errors": [], "done": []}
    _, messages = sug.build_prompt(signals)
    assert "уже реализовано" in messages[0]["content"].lower()


def test_build_prompt_system_instructs_to_skip_items_already_done():
    system, _ = sug.build_prompt({"regress": "x", "backlog": [], "errors": [], "done": []})
    assert "уже реализовано" in system.lower()
    assert "пропусти" in system.lower() or "не предлагай" in system.lower()


# ── parse_suggestions ────────────────────────────────────────────────────────
_GOOD_REPLY = """Вот предложения:
[
  {"title": "Таргет-режим регресса", "signal": "бэклог", "rationale": "ускорит гейт",
   "draft": "Реализуй таргет-режим регресса по диффу", "size": "M"},
  {"title": "Изоляция тест-уведомлений", "signal": "бэклог", "rationale": "протечка в прод-чат",
   "draft": "Изолируй тестовые уведомления от прод-чата", "size": "S"},
  {"title": "Chase failing tests", "signal": "регресс", "rationale": "33 failed",
   "draft": "Почини топ падающих тестов регресса", "size": "L"}
]
Конец.
"""


def test_parse_suggestions_extracts_valid_json_array():
    out = sug.parse_suggestions(_GOOD_REPLY)
    assert len(out) == 3
    assert out[0]["title"] == "Таргет-режим регресса"
    assert out[0]["size"] == "M"
    assert out[1]["size"] == "S"
    assert out[2]["size"] == "L"


def test_parse_suggestions_caps_at_three():
    items = [
        {"title": f"T{i}", "signal": "s", "rationale": "r", "draft": f"d{i}", "size": "M"}
        for i in range(5)
    ]
    import json
    reply = json.dumps(items)
    out = sug.parse_suggestions(reply)
    assert len(out) == 3


def test_parse_suggestions_drops_entries_missing_required_fields():
    reply = '[{"title": "no draft here", "signal": "s", "rationale": "r", "size": "M"}]'
    assert sug.parse_suggestions(reply) == []


def test_parse_suggestions_defaults_invalid_size_to_m():
    reply = '[{"title": "t", "signal": "s", "rationale": "r", "draft": "d", "size": "XL"}]'
    out = sug.parse_suggestions(reply)
    assert out[0]["size"] == "M"


def test_parse_suggestions_returns_empty_on_garbage():
    assert sug.parse_suggestions("not json at all") == []
    assert sug.parse_suggestions("") == []
    assert sug.parse_suggestions(None) == []


def test_parse_suggestions_returns_empty_on_non_list_json():
    assert sug.parse_suggestions('{"title": "not a list"}') == []


# ── parse_suggestions: dirty real-world variants (live-incident hardening) ──
# The 15:57 prod incident (paid call succeeded, parse still failed) never
# persisted the raw reply anywhere in logs — see report.md. These variants
# cover the failure modes a Sonnet reply realistically hits: markdown code
# fences, prose preambles/trailers around the fences, and max_tokens
# truncation cutting the array off mid-object.
_ONE_ITEM_JSON = ('{"title": "T", "signal": "s", "rationale": "r", '
                   '"draft": "d", "size": "M"}')


def test_parse_suggestions_strips_markdown_json_fence():
    reply = "```json\n[%s]\n```" % _ONE_ITEM_JSON
    out = sug.parse_suggestions(reply)
    assert len(out) == 1
    assert out[0]["title"] == "T"


def test_parse_suggestions_strips_preamble_and_trailer_around_fence():
    reply = "Конечно! Вот топ-3:\n```json\n[%s]\n```\nНадеюсь, помогло!" % _ONE_ITEM_JSON
    out = sug.parse_suggestions(reply)
    assert len(out) == 1
    assert out[0]["title"] == "T"


def test_parse_suggestions_strips_plain_fence_without_json_tag():
    reply = "```\n[%s]\n```" % _ONE_ITEM_JSON
    out = sug.parse_suggestions(reply)
    assert len(out) == 1


def test_parse_suggestions_salvages_truncated_array_mid_object():
    # simulates a max_tokens cutoff: first object complete, second cut mid-string
    reply = (
        '[{"title": "T1", "signal": "s", "rationale": "r", "draft": "d1", "size": "S"},'
        '{"title": "T2", "signal": "s", "rationale": "r", "draft": "d2 cut off without closing'
    )
    out = sug.parse_suggestions(reply)
    assert len(out) == 1
    assert out[0]["title"] == "T1"


def test_parse_suggestions_salvages_truncated_array_cut_right_after_comma():
    reply = '[{"title": "T1", "signal": "s", "rationale": "r", "draft": "d1", "size": "S"},'
    out = sug.parse_suggestions(reply)
    assert len(out) == 1
    assert out[0]["title"] == "T1"


def test_parse_suggestions_returns_empty_when_first_object_itself_is_truncated():
    reply = '[{"title": "T1", "signal": "s", "rationale": "r", "draft": "cut mid str'
    assert sug.parse_suggestions(reply) == []


# ── find_duplicate_match / flag_duplicate_suggestions (v0.3 dedup gate) ─────
_DONE_RECORDS = [
    {"title": "Изоляция log-контаминации test-pollution", "date": "2026-07-10"},
    {"title": "Регресс-сторож RAM-guard + батчи", "date": None},
]


def test_find_duplicate_match_finds_close_wording():
    match = sug.find_duplicate_match("Изолировать log-контаминацию между тестами", _DONE_RECORDS)
    assert match is not None
    assert match["title"] == "Изоляция log-контаминации test-pollution"


def test_find_duplicate_match_returns_none_for_fresh_topic():
    match = sug.find_duplicate_match("Добавить дашборд метрик Instagram Reels", _DONE_RECORDS)
    assert match is None


def test_find_duplicate_match_returns_none_on_empty_done_records():
    assert sug.find_duplicate_match("Что угодно", []) is None
    assert sug.find_duplicate_match("Что угодно", None) is None


def test_flag_duplicate_suggestions_tags_duplicate_candidate():
    suggestions = [
        {"title": "Изолировать log-контаминацию между тестами", "signal": "s", "rationale": "r",
         "draft": "d", "size": "M"},
    ]
    out = sug.flag_duplicate_suggestions(suggestions, _DONE_RECORDS)
    assert "duplicate_warning" in out[0]
    assert "⚠️ возможно уже решено" in out[0]["duplicate_warning"]
    assert "2026-07-10" in out[0]["duplicate_warning"]


def test_flag_duplicate_suggestions_leaves_fresh_candidate_unflagged():
    suggestions = [
        {"title": "Добавить дашборд метрик Instagram Reels", "signal": "s", "rationale": "r",
         "draft": "d", "size": "M"},
    ]
    out = sug.flag_duplicate_suggestions(suggestions, _DONE_RECORDS)
    assert "duplicate_warning" not in out[0]


def test_flag_duplicate_suggestions_mixed_batch_flags_only_the_duplicate():
    suggestions = [
        {"title": "Изолировать log-контаминацию между тестами", "signal": "s", "rationale": "r",
         "draft": "d1", "size": "M"},
        {"title": "Добавить дашборд метрик Instagram Reels", "signal": "s", "rationale": "r",
         "draft": "d2", "size": "M"},
    ]
    out = sug.flag_duplicate_suggestions(suggestions, _DONE_RECORDS)
    assert len(out) == 2                                    # never dropped, both survive
    assert "duplicate_warning" in out[0]
    assert "duplicate_warning" not in out[1]


def test_flag_duplicate_suggestions_does_not_mutate_input():
    suggestions = [
        {"title": "Изолировать log-контаминацию между тестами", "signal": "s", "rationale": "r",
         "draft": "d", "size": "M"},
    ]
    sug.flag_duplicate_suggestions(suggestions, _DONE_RECORDS)
    assert "duplicate_warning" not in suggestions[0]         # original dict untouched


# ── count_matched_backlog_items / flag_packaged_suggestions (v0.3: no packing) ─
_TWO_BACKLOG_ITEMS = [
    "Ancestor-check влитых веток в конвейере",
    "Изоляция тестовых уведомлений от прод-чата",
]


def test_count_matched_backlog_items_counts_distinct_matches():
    draft = ("Реализуй ancestor-check влитых веток в конвейере, а заодно "
             "изоляцию тестовых уведомлений от прод-чата в одном PR")
    assert sug.count_matched_backlog_items(draft, _TWO_BACKLOG_ITEMS) == 2


def test_count_matched_backlog_items_single_topic_counts_one():
    draft = "Реализуй ancestor-check влитых веток в конвейере"
    assert sug.count_matched_backlog_items(draft, _TWO_BACKLOG_ITEMS) == 1


def test_flag_packaged_suggestions_tags_multi_issue_draft():
    suggestions = [{
        "title": "Ancestor-check + изоляция уведомлений", "signal": "s", "rationale": "r",
        "draft": ("Реализуй ancestor-check влитых веток в конвейере, а заодно "
                  "изоляцию тестовых уведомлений от прод-чата"),
        "size": "M",
    }]
    out = sug.flag_packaged_suggestions(suggestions, _TWO_BACKLOG_ITEMS)
    assert "packaged_warning" in out[0]


def test_flag_packaged_suggestions_leaves_single_issue_draft_unflagged():
    suggestions = [{
        "title": "Ancestor-check", "signal": "s", "rationale": "r",
        "draft": "Реализуй ancestor-check влитых веток в конвейере", "size": "M",
    }]
    out = sug.flag_packaged_suggestions(suggestions, _TWO_BACKLOG_ITEMS)
    assert "packaged_warning" not in out[0]


# ── build_prompt: hardened system instructions ──────────────────────────────
def test_build_prompt_system_forbids_markdown_wrapping():
    system, _ = sug.build_prompt({"regress": "x", "backlog": [], "errors": []})
    assert "```" in system or "markdown" in system.lower() or "фенс" in system.lower() \
        or "оболоч" in system.lower()


# ── build_prompt: one-issue-per-draft rule (v0.3) ────────────────────────────
def test_build_prompt_system_forbids_packaging_multiple_issues():
    system, _ = sug.build_prompt({"regress": "x", "backlog": [], "errors": [], "done": []})
    assert "одна проблема" in system.lower() or "не склеивай" in system.lower()


# ── output token budget ──────────────────────────────────────────────────────
def test_max_output_tokens_raised_to_fit_full_top3_json():
    assert sug.MAX_OUTPUT_TOKENS >= 3000


# ── format_suggestions ───────────────────────────────────────────────────────
def test_format_suggestions_renders_all_fields():
    suggestions = [
        {"title": "Таргет-режим", "signal": "бэклог", "rationale": "ускорит гейт",
         "draft": "Реализуй таргет-режим", "size": "M"},
    ]
    text = sug.format_suggestions(suggestions)
    assert "Таргет-режим" in text
    assert "бэклог" in text
    assert "ускорит гейт" in text
    assert "Реализуй таргет-режим" in text
    assert "M" in text


def test_format_suggestions_empty_list_is_honest_not_fabricated():
    text = sug.format_suggestions([])
    assert text  # non-empty message
    assert "не удалось" in text.lower() or "пуст" in text.lower()


def test_format_suggestions_empty_list_includes_raw_reply_snippet_for_diagnosis():
    raw = "x" * 200 + "TAIL_MARKER_BEYOND_200_CHARS"
    text = sug.format_suggestions([], raw_reply=raw)
    assert "не удалось" in text.lower()
    assert raw[:200] in text
    assert "TAIL_MARKER_BEYOND_200_CHARS" not in text  # capped at 200 chars


def test_format_suggestions_empty_list_without_raw_reply_still_honest():
    text = sug.format_suggestions([], raw_reply=None)
    assert "не удалось" in text.lower()


# ── format_suggestion_card (one-tap /suggest_tasks UX) ──────────────────────
# The compact per-suggestion card sent to Telegram: title/size/signal/
# rationale ONLY — never the (potentially long) draft, which is what used to
# get silently truncated by ``send``'s 3900-char cap and made uncopyable.
def test_format_suggestion_card_renders_title_size_signal_rationale():
    s = {"title": "Таргет-режим", "signal": "бэклог", "rationale": "ускорит гейт",
         "draft": "Реализуй таргет-режим по диффу " * 50, "size": "M"}
    text = sug.format_suggestion_card(s, 0, 3)
    assert "Таргет-режим" in text
    assert "бэклог" in text
    assert "ускорит гейт" in text
    assert "M" in text
    assert "1" in text and "3" in text          # 1/3 position marker


def test_format_suggestion_card_never_includes_the_draft_body():
    s = {"title": "T", "signal": "s", "rationale": "r",
         "draft": "UNIQUE_DRAFT_MARKER_TEXT", "size": "S"}
    text = sug.format_suggestion_card(s, 0, 1)
    assert "UNIQUE_DRAFT_MARKER_TEXT" not in text


def test_format_suggestion_card_handles_missing_signal_and_rationale():
    s = {"title": "T", "draft": "d", "size": "S"}
    text = sug.format_suggestion_card(s, 0, 1)
    assert text  # no crash on missing optional fields


# ── format_suggestion_card: v0.3 duplicate/packaged warnings surface to admin ─
def test_format_suggestion_card_shows_duplicate_warning():
    s = {"title": "T", "signal": "s", "rationale": "r", "draft": "d", "size": "S",
         "duplicate_warning": "⚠️ возможно уже решено: Old fix (2026-07-10)"}
    text = sug.format_suggestion_card(s, 0, 1)
    assert "⚠️ возможно уже решено: Old fix (2026-07-10)" in text


def test_format_suggestion_card_shows_packaged_warning():
    s = {"title": "T", "signal": "s", "rationale": "r", "draft": "d", "size": "S",
         "packaged_warning": "⚠️ похоже, черновик объединяет несколько пунктов бэклога — раздели на отдельные /dev_task"}
    text = sug.format_suggestion_card(s, 0, 1)
    assert "объединяет несколько пунктов" in text


def test_format_suggestion_card_no_warning_when_absent():
    s = {"title": "T", "signal": "s", "rationale": "r", "draft": "d", "size": "S"}
    text = sug.format_suggestion_card(s, 0, 1)
    assert "⚠️" not in text


# ── build_suggestions_state / resolve_suggestion (stale-guard) ──────────────
def test_build_suggestions_state_carries_gen_id_and_suggestions():
    suggestions = [{"title": "T", "signal": "s", "rationale": "r", "draft": "d", "size": "M"}]
    state = sug.build_suggestions_state(suggestions, "gen1", "2026-07-12T08:00:00")
    assert state["gen_id"] == "gen1"
    assert state["suggestions"] == suggestions
    assert state["created_at"] == "2026-07-12T08:00:00"


def test_resolve_suggestion_returns_item_for_matching_gen_id():
    suggestions = [
        {"title": "T0", "signal": "s", "rationale": "r", "draft": "d0", "size": "S"},
        {"title": "T1", "signal": "s", "rationale": "r", "draft": "d1", "size": "M"},
    ]
    state = sug.build_suggestions_state(suggestions, "gen1", "2026-07-12T08:00:00")
    assert sug.resolve_suggestion(state, "gen1", 1)["draft"] == "d1"


def test_resolve_suggestion_returns_none_on_stale_gen_id():
    suggestions = [{"title": "T", "signal": "s", "rationale": "r", "draft": "d", "size": "M"}]
    state = sug.build_suggestions_state(suggestions, "gen1", "2026-07-12T08:00:00")
    assert sug.resolve_suggestion(state, "gen0_old", 0) is None


def test_resolve_suggestion_returns_none_on_out_of_range_index():
    suggestions = [{"title": "T", "signal": "s", "rationale": "r", "draft": "d", "size": "M"}]
    state = sug.build_suggestions_state(suggestions, "gen1", "2026-07-12T08:00:00")
    assert sug.resolve_suggestion(state, "gen1", 5) is None
    assert sug.resolve_suggestion(state, "gen1", -1) is None


def test_resolve_suggestion_returns_none_on_missing_state():
    assert sug.resolve_suggestion(None, "gen1", 0) is None
    assert sug.resolve_suggestion({}, "gen1", 0) is None
