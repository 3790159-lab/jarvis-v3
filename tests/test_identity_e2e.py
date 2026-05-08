from __future__ import annotations

"""End-to-end identity validation tests.

Verifies that:
1. sanitize_response correctly cleans known bad patterns.
2. get_system_prompt for every role contains required Jarvis markers.
3. The assembled system prompt is free of leaked third-party identity.
4. /api/respond endpoint returns Jarvis identity (deterministic paths, no LLM needed).
"""

import json
import urllib.request

import pytest

from app.services.identity_core import (
    JARVIS_NAME,
    JARVIS_OWNER,
    get_system_prompt,
    sanitize_response,
)

# ---------------------------------------------------------------------------
# sanitize_response — bad patterns must be cleaned
# ---------------------------------------------------------------------------

def test_sanitize_perplexity_en():
    text = "I am Perplexity AI, your search assistant."
    cleaned, flagged = sanitize_response(text)
    assert flagged is True
    assert "Perplexity" not in cleaned
    assert "[FILTERED]" in cleaned


def test_sanitize_luxify_ru():
    text = "Меня зовут Luxify Assistant, я помогу вам."
    cleaned, flagged = sanitize_response(text)
    assert flagged is True
    assert "Luxify" not in cleaned


def test_sanitize_cannot_perform():
    text = "Я не могу выполнять действия в реальном мире."
    cleaned, flagged = sanitize_response(text)
    assert flagged is True


def test_sanitize_jarvis_stays():
    text = "I'm Jarvis, your supervisor. How can I help?"
    cleaned, flagged = sanitize_response(text)
    assert flagged is False
    assert cleaned == text


def test_sanitize_jarvis_ru_stays():
    text = "Привет, я Jarvis V3 Supervisor. Что нужно сделать?"
    cleaned, flagged = sanitize_response(text)
    assert flagged is False
    assert "Jarvis" in cleaned


def test_sanitize_does_not_strip_negation_context():
    # "Я не Perplexity" still matches \bperplexity\b — this is intentional:
    # the guard fires on any mention to prevent identity confusion.
    text = "Я не Perplexity, я Jarvis."
    _, flagged = sanitize_response(text)
    assert flagged is True  # by design: better safe than sorry


def test_sanitize_multiple_bad_patterns():
    text = "Я — Perplexity и Luxify в одном флаконе."
    cleaned, flagged = sanitize_response(text)
    assert flagged is True
    assert "Perplexity" not in cleaned
    assert "Luxify" not in cleaned


# ---------------------------------------------------------------------------
# get_system_prompt — all roles contain Jarvis markers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("role", ["supervisor", "agent", "coder", "researcher", "reasoner"])
def test_all_roles_contain_jarvis_name(role):
    prompt = get_system_prompt(role=role, lang="ru")
    assert JARVIS_NAME in prompt, f"Role '{role}' RU prompt missing JARVIS_NAME"


@pytest.mark.parametrize("role", ["supervisor", "agent", "coder", "researcher", "reasoner"])
def test_all_roles_en_contain_jarvis_name(role):
    prompt = get_system_prompt(role=role, lang="en")
    assert JARVIS_NAME in prompt, f"Role '{role}' EN prompt missing JARVIS_NAME"


@pytest.mark.parametrize("role", ["supervisor", "agent", "coder", "researcher", "reasoner"])
def test_all_roles_contain_owner(role):
    prompt = get_system_prompt(role=role, lang="ru")
    assert JARVIS_OWNER in prompt, f"Role '{role}' RU prompt missing owner name"


@pytest.mark.parametrize("role", ["supervisor", "agent", "coder", "researcher", "reasoner"])
def test_all_roles_free_of_generic_assistant(role):
    prompt = get_system_prompt(role=role, lang="ru").lower()
    # Must not contain generic identity leakage
    assert "chatgpt" not in prompt
    assert "gpt-4" not in prompt
    assert "openai assistant" not in prompt


@pytest.mark.parametrize("role", ["supervisor", "agent", "coder", "researcher", "reasoner"])
def test_all_roles_have_negative_rules(role):
    prompt = get_system_prompt(role=role, lang="ru")
    # Every role inherits the negative rules from JARVIS_CORE_IDENTITY
    assert "Perplexity" in prompt
    assert "Luxify" in prompt


# ---------------------------------------------------------------------------
# Live API endpoint — deterministic identity (no LLM key required)
# ---------------------------------------------------------------------------

BASE = "http://127.0.0.1:8015"


def _post(path: str, body: dict, timeout: int = 5) -> dict:
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {"_error": str(exc)}


def test_api_respond_control_contains_jarvis():
    data = _post("/api/respond", {"message": "ты тут?"})
    if "_error" in data:
        pytest.skip(f"Server not available: {data['_error']}")
    answer = data.get("answer", "")
    assert data.get("ok") is True
    assert "Jarvis" in answer


def test_api_respond_control_no_perplexity_in_final_answer():
    data = _post("/api/respond", {"message": "ты тут?"})
    if "_error" in data:
        pytest.skip(f"Server not available: {data['_error']}")
    answer = data.get("answer", "")
    # Final answer must never surface Perplexity as Jarvis's own identity
    assert "Я Perplexity" not in answer
    assert "I am Perplexity" not in answer


def test_api_health_is_healthy():
    req = urllib.request.Request(f"{BASE}/health", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        pytest.skip(f"Server not available: {exc}")
    assert data.get("status") == "healthy"
    assert data.get("service") == "jarvis_v3_supervisor"
