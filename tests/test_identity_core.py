from __future__ import annotations

import pytest

from app.services.identity_core import (
    BAD_IDENTITY_PATTERNS,
    JARVIS_CORE_IDENTITY,
    JARVIS_CORE_IDENTITY_EN,
    JARVIS_NAME,
    JARVIS_OWNER,
    CAPABILITIES,
    MODES,
    get_system_prompt,
    sanitize_response,
)


# ---------------------------------------------------------------------------
# Constants smoke tests
# ---------------------------------------------------------------------------

def test_jarvis_name_not_empty():
    assert JARVIS_NAME and "Jarvis" in JARVIS_NAME


def test_core_identity_ru_contains_jarvis():
    assert "Jarvis" in JARVIS_CORE_IDENTITY


def test_core_identity_ru_has_negative_rules():
    assert "Perplexity" in JARVIS_CORE_IDENTITY
    assert "Luxify" in JARVIS_CORE_IDENTITY


def test_core_identity_ru_mentions_owner():
    assert JARVIS_OWNER in JARVIS_CORE_IDENTITY


def test_core_identity_en_contains_jarvis():
    assert "Jarvis" in JARVIS_CORE_IDENTITY_EN


def test_core_identity_en_has_negative_rules():
    assert "Perplexity" in JARVIS_CORE_IDENTITY_EN
    assert "Luxify" in JARVIS_CORE_IDENTITY_EN


def test_bad_identity_patterns_not_empty():
    assert len(BAD_IDENTITY_PATTERNS) > 0


def test_capabilities_and_modes_not_empty():
    assert len(CAPABILITIES) > 0
    assert len(MODES) > 0


# ---------------------------------------------------------------------------
# get_system_prompt — all roles
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("role", ["supervisor", "agent", "coder", "researcher", "reasoner"])
def test_get_system_prompt_ru_contains_jarvis(role):
    prompt = get_system_prompt(role=role, lang="ru")
    assert isinstance(prompt, str)
    assert len(prompt) > 50
    assert "Jarvis" in prompt


@pytest.mark.parametrize("role", ["supervisor", "agent", "coder", "researcher", "reasoner"])
def test_get_system_prompt_en_contains_jarvis(role):
    prompt = get_system_prompt(role=role, lang="en")
    assert isinstance(prompt, str)
    assert len(prompt) > 50
    assert "Jarvis" in prompt


def test_supervisor_has_no_extra_suffix():
    ru = get_system_prompt("supervisor", lang="ru")
    en = get_system_prompt("supervisor", lang="en")
    # supervisor role adds no suffix — prompt equals stripped core
    assert ru == JARVIS_CORE_IDENTITY.strip()
    assert en == JARVIS_CORE_IDENTITY_EN.strip()


def test_coder_role_mentions_code():
    ru = get_system_prompt("coder", lang="ru")
    en = get_system_prompt("coder", lang="en")
    assert "код" in ru.lower() or "code" in ru.lower()
    assert "code" in en.lower()


def test_researcher_role_mentions_research():
    ru = get_system_prompt("researcher", lang="ru")
    en = get_system_prompt("researcher", lang="en")
    assert "исследован" in ru.lower() or "research" in ru.lower()
    assert "research" in en.lower()


def test_reasoner_role_mentions_analysis():
    ru = get_system_prompt("reasoner", lang="ru")
    en = get_system_prompt("reasoner", lang="en")
    assert "анализ" in ru.lower() or "reasoning" in ru.lower()
    assert "analysis" in en.lower() or "reasoning" in en.lower()


# ---------------------------------------------------------------------------
# get_system_prompt — provider hints
# ---------------------------------------------------------------------------

def test_provider_hint_anthropic_appended():
    without = get_system_prompt("supervisor", lang="ru")
    with_hint = get_system_prompt("supervisor", lang="ru", provider_hint="anthropic")
    assert len(with_hint) > len(without)
    assert "Claude" in with_hint


def test_provider_hint_openai_appended():
    without = get_system_prompt("supervisor", lang="ru")
    with_hint = get_system_prompt("supervisor", lang="ru", provider_hint="openai")
    assert len(with_hint) > len(without)


def test_provider_hint_ignored_for_en():
    without = get_system_prompt("supervisor", lang="en")
    with_hint = get_system_prompt("supervisor", lang="en", provider_hint="anthropic")
    # hints only appended for Russian prompts
    assert without == with_hint


# ---------------------------------------------------------------------------
# sanitize_response
# ---------------------------------------------------------------------------

def test_sanitize_clean_text():
    text = "Привет от Jarvis. Готов помочь."
    cleaned, was_sanitized = sanitize_response(text)
    assert cleaned == text
    assert was_sanitized is False


def test_sanitize_detects_perplexity():
    text = "I am Perplexity, your search assistant."
    cleaned, was_sanitized = sanitize_response(text)
    assert was_sanitized is True
    assert "Perplexity" not in cleaned
    assert "[FILTERED]" in cleaned


def test_sanitize_detects_luxify():
    text = "Я — Luxify, твой помощник."
    cleaned, was_sanitized = sanitize_response(text)
    assert was_sanitized is True
    assert "Luxify" not in cleaned


def test_sanitize_detects_cannot_perform():
    text = "Я не могу выполнять действия в реальном мире."
    cleaned, was_sanitized = sanitize_response(text)
    assert was_sanitized is True


def test_sanitize_case_insensitive():
    text = "Hello from PERPLEXITY assistant"
    _, was_sanitized = sanitize_response(text)
    assert was_sanitized is True


def test_sanitize_empty_string():
    cleaned, was_sanitized = sanitize_response("")
    assert cleaned == ""
    assert was_sanitized is False


def test_sanitize_returns_tuple():
    result = sanitize_response("some text")
    assert isinstance(result, tuple)
    assert len(result) == 2
