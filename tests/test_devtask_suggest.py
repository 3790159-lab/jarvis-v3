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


# ── build_prompt ───────────────────────────────────────────────────────────────
def test_build_prompt_returns_system_and_user_message():
    signals = {"regress": "33 failed", "backlog": ["item A"], "errors": ["err B"]}
    system, messages = sug.build_prompt(signals)
    assert "JSON" in system
    assert isinstance(messages, list) and len(messages) == 1
    assert messages[0]["role"] == "user"
    assert "item A" in messages[0]["content"]
    assert "err B" in messages[0]["content"]
    assert "33 failed" in messages[0]["content"]


def test_build_prompt_handles_empty_signals():
    system, messages = sug.build_prompt({"regress": "нет данных", "backlog": [], "errors": []})
    assert messages[0]["content"]  # no crash on empty lists


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


# ── build_prompt: hardened system instructions ──────────────────────────────
def test_build_prompt_system_forbids_markdown_wrapping():
    system, _ = sug.build_prompt({"regress": "x", "backlog": [], "errors": []})
    assert "```" in system or "markdown" in system.lower() or "фенс" in system.lower() \
        or "оболоч" in system.lower()


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
