# -*- coding: utf-8 -*-
"""Этап 3, кирпич #1 — генератор IG-подписей: pure prompt/parse/validate module.

$0, no network — the billable LLM call is injected via ``ask_llm`` (mirrors
app/services/devtask/suggest.py's isolation) so every test here mocks it,
never touching the real Anthropic client.
"""
import pytest

from app.services import ig_caption as cap


# ── build_prompt ─────────────────────────────────────────────────────────────
def test_build_prompt_includes_business_topic_tone_cta():
    system, messages = cap.build_prompt({
        "business": "Кав'ярня Ранок",
        "topic": "новий сезонний напій",
        "tone": ["теплий", "грайливий"],
        "cta": "забронюй столик у директ",
    })
    assert "Кав'ярня Ранок" in messages[0]["content"]
    assert "новий сезонний напій" in messages[0]["content"]
    assert "теплий" in messages[0]["content"] and "грайливий" in messages[0]["content"]
    assert "забронюй столик у директ" in messages[0]["content"]


def test_build_prompt_defaults_hashtags_count_and_lang():
    system, _ = cap.build_prompt({"business": "B", "topic": "T"})
    assert str(cap.DEFAULT_HASHTAGS_COUNT) in system
    assert cap.DEFAULT_LANG in system.lower()


def test_build_prompt_honors_explicit_hashtags_count_and_lang():
    system, _ = cap.build_prompt({"business": "B", "topic": "T", "hashtags_count": 5, "lang": "en"})
    assert "5" in system
    assert "en" in system.lower()


def test_build_prompt_includes_forbidden_topics():
    system, _ = cap.build_prompt({
        "business": "B", "topic": "T", "forbidden": ["алкоголь", "політика"],
    })
    assert "алкоголь" in system
    assert "політика" in system


def test_build_prompt_no_forbidden_section_when_absent():
    system, _ = cap.build_prompt({"business": "B", "topic": "T"})
    assert "forbidden" not in system.lower()


def test_build_prompt_system_forbids_fences_and_preamble():
    system, _ = cap.build_prompt({"business": "B", "topic": "T"})
    assert "фенс" in system.lower() or "```" in system


# ── clean_caption ─────────────────────────────────────────────────────────────
def test_clean_caption_strips_code_fences():
    raw = "```\nСмачна кава чекає на вас! ☕ #кава #ранок\n```"
    assert cap.clean_caption(raw) == "Смачна кава чекає на вас! ☕ #кава #ранок"


def test_clean_caption_strips_language_tagged_fence():
    raw = "```text\nПривіт світ #test\n```"
    assert cap.clean_caption(raw) == "Привіт світ #test"


def test_clean_caption_strips_leading_preamble():
    raw = "Ось підпис:\nСмачна кава чекає на вас! ☕ #кава"
    assert cap.clean_caption(raw) == "Смачна кава чекає на вас! ☕ #кава"


def test_clean_caption_handles_plain_text_unchanged():
    raw = "Просто текст без обгорток #ok"
    assert cap.clean_caption(raw) == "Просто текст без обгорток #ok"


def test_clean_caption_handles_none_and_empty():
    assert cap.clean_caption(None) == ""
    assert cap.clean_caption("") == ""
    assert cap.clean_caption("   \n  ") == ""


# ── count_hashtags ────────────────────────────────────────────────────────────
def test_count_hashtags_counts_all_tags():
    text = "Гарний ранок! ☕ #кава #ранок #затишок #кавярня"
    assert cap.count_hashtags(text) == 4


def test_count_hashtags_zero_when_none():
    assert cap.count_hashtags("Текст без хештегів.") == 0


def test_count_hashtags_empty_string():
    assert cap.count_hashtags("") == 0


# ── enforce_length ────────────────────────────────────────────────────────────
def test_enforce_length_leaves_short_caption_untouched():
    short = "Короткий підпис #ok"
    assert cap.enforce_length(short) == short


def test_enforce_length_truncates_to_ig_limit():
    long_caption = "А" * 3000
    out = cap.enforce_length(long_caption)
    assert len(out) <= cap.IG_CAPTION_MAX_LEN


def test_ig_caption_max_len_is_2200():
    assert cap.IG_CAPTION_MAX_LEN == 2200


# ── generate_caption ──────────────────────────────────────────────────────────
def test_generate_caption_calls_ask_llm_once_and_cleans_reply():
    calls = {"n": 0}

    def _fake_ask_llm(system, messages):
        calls["n"] += 1
        return "```\nСмачна кава чекає! ☕ #кава #ранок\n```"

    out = cap.generate_caption(
        {"business": "Кав'ярня Ранок", "topic": "новинка"}, ask_llm=_fake_ask_llm,
    )
    assert calls["n"] == 1
    assert out == "Смачна кава чекає! ☕ #кава #ранок"
    assert "```" not in out


def test_generate_caption_enforces_ig_length_limit():
    def _fake_ask_llm(system, messages):
        return "А" * 3000 + " #кава"

    out = cap.generate_caption({"business": "B", "topic": "T"}, ask_llm=_fake_ask_llm)
    assert len(out) <= cap.IG_CAPTION_MAX_LEN


def test_generate_caption_hashtags_are_countable_on_result():
    def _fake_ask_llm(system, messages):
        return "Раночок починається смачно! #кава #ранок #затишок #кавярняКиїв #latte #espresso #cozy #ukraine"

    out = cap.generate_caption({"business": "B", "topic": "T", "hashtags_count": 8}, ask_llm=_fake_ask_llm)
    assert cap.count_hashtags(out) == 8


def test_generate_caption_without_ask_llm_raises_instead_of_hitting_network():
    # Money-safety: the pure module must NEVER silently default to a real
    # Anthropic call. Missing ask_llm is a caller bug -> fail loud, not
    # a live spend.
    with pytest.raises(TypeError):
        cap.generate_caption({"business": "B", "topic": "T"})


def test_generate_caption_passes_forbidden_into_prompt():
    captured = {}

    def _fake_ask_llm(system, messages):
        captured["system"] = system
        return "ok #tag"

    cap.generate_caption(
        {"business": "B", "topic": "T", "forbidden": ["політика"]}, ask_llm=_fake_ask_llm,
    )
    assert "політика" in captured["system"]


# ── apply_brand ───────────────────────────────────────────────────────────────
def test_apply_brand_none_returns_brief_unchanged():
    brief = {"business": "наш бізнес", "topic": "T", "cta": "напиши в директ"}
    merged = cap.apply_brand(brief, None)
    assert merged == brief
    assert merged is not brief  # honest copy, caller's dict not mutated


def test_apply_brand_overrides_business_tone_lang_cta_forbidden_hashtags():
    brief = {"business": "наш бізнес", "topic": "T", "cta": "напиши в директ"}
    brand = {
        "business": "Віра — ШІ-аватар",
        "tone": ["молодий", "енергійний"],
        "lang": "uk",
        "cta": "напиши в директ, якщо хочеш такий самий ШІ-контент",
        "forbidden": ["політика", "релігія"],
        "hashtags_count": 7,
    }
    merged = cap.apply_brand(brief, brand)
    assert merged["business"] == "Віра — ШІ-аватар"
    assert merged["tone"] == ["молодий", "енергійний"]
    assert merged["lang"] == "uk"
    assert merged["cta"] == "напиши в директ, якщо хочеш такий самий ШІ-контент"
    assert merged["forbidden"] == ["політика", "релігія"]
    assert merged["hashtags_count"] == 7
    assert merged["topic"] == "T"  # topic never overridden by brand


def test_apply_brand_never_overrides_topic():
    brief = {"business": "B", "topic": "справжня тема юзера"}
    brand = {"topic": "має бути проігноровано"}
    merged = cap.apply_brand(brief, brand)
    assert merged["topic"] == "справжня тема юзера"


def test_apply_brand_ignores_empty_brand_fields():
    brief = {"business": "наш бізнес", "topic": "T"}
    brand = {"business": "", "forbidden": [], "cta": None}
    merged = cap.apply_brand(brief, brand)
    assert merged["business"] == "наш бізнес"
    assert "forbidden" not in merged
    assert "cta" not in merged


def test_generate_caption_with_brand_reaches_llm_prompt():
    captured = {}

    def _fake_ask_llm(system, messages):
        captured["system"] = system
        captured["messages"] = messages
        return "ok #tag"

    brief = cap.apply_brand(
        {"business": "наш бізнес", "topic": "новий пост"},
        {"tone": ["молодий", "енергійний"], "lang": "uk"},
    )
    cap.generate_caption(brief, ask_llm=_fake_ask_llm)
    assert "молодий" in captured["messages"][0]["content"]
    assert "енергійний" in captured["messages"][0]["content"]
    assert "uk" in captured["system"].lower()
