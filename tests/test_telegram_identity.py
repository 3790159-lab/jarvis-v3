from __future__ import annotations

"""Phase 5+6+7: Identity Guard, Capability Registry, and Honest Fallback tests.

Verifies:
1. classify_message() routes identity questions to intent="identity"
2. classify_message() does NOT route non-identity messages to intent="identity"
3. identity_answer() contains required Jarvis markers and no leaked third-party names
4. classify_message() routes capability questions to intent="capabilities"
5. capabilities_text() is built from CAPABILITY_REGISTRY and contains real provider names
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.jarvis_smart_telegram_control import (
    CAPABILITIES,
    CAPABILITY_STATUS_PARTIAL,
    JARVIS_NAME,
    JARVIS_OWNER,
    JARVIS_VERSION,
    capabilities_text,
    classify_message,
    identity_answer,
)
import tools.jarvis_smart_telegram_control as _mod


# ---------------------------------------------------------------------------
# classify_message — identity questions must return intent="identity"
# ---------------------------------------------------------------------------

def test_classify_kak_tebya_zovut():
    result = classify_message("Как тебя зовут?", {})
    assert result["intent"] == "identity", f"expected identity, got {result}"


def test_classify_kto_ty():
    result = classify_message("кто ты?", {})
    assert result["intent"] == "identity", f"expected identity, got {result}"


def test_classify_ty_tut():
    # H9.2: presence checks go to self_status, not identity
    result = classify_message("ты тут?", {})
    assert result["intent"] == "self_status", f"expected self_status (presence check), got {result}"


def test_classify_jarvis_tut():
    # H9.2: presence checks go to self_status, not identity
    result = classify_message("Джарвис тут?", {})
    assert result["intent"] == "self_status", f"expected self_status (presence check), got {result}"


def test_classify_predstavsya():
    result = classify_message("представься", {})
    assert result["intent"] == "identity", f"expected identity, got {result}"


def test_classify_kto_ty_takoy():
    result = classify_message("Кто ты такой?", {})
    assert result["intent"] == "identity", f"expected identity, got {result}"


def test_classify_ty_jarvis():
    result = classify_message("ты jarvis?", {})
    assert result["intent"] == "identity", f"expected identity, got {result}"


def test_classify_ty_dzharvis():
    result = classify_message("ты джарвис?", {})
    assert result["intent"] == "identity", f"expected identity, got {result}"


def test_classify_tvoe_imya():
    result = classify_message("твоё имя?", {})
    assert result["intent"] == "identity", f"expected identity, got {result}"


def test_classify_who_are_you():
    result = classify_message("who are you?", {})
    assert result["intent"] == "identity", f"expected identity, got {result}"


def test_classify_what_are_you():
    result = classify_message("what are you?", {})
    assert result["intent"] == "identity", f"expected identity, got {result}"


def test_classify_your_name():
    result = classify_message("your name?", {})
    assert result["intent"] == "identity", f"expected identity, got {result}"


def test_classify_ty_zdes():
    # H9.2: presence check goes to self_status, not identity
    result = classify_message("ты здесь?", {})
    assert result["intent"] == "self_status", f"expected self_status (presence check), got {result}"


# ---------------------------------------------------------------------------
# classify_message — non-identity messages must NOT return intent="identity"
# ---------------------------------------------------------------------------

def test_classify_sravni_not_identity():
    result = classify_message("Сравни FAL и Replicate", {})
    assert result["intent"] != "identity", f"should not be identity, got {result}"


def test_classify_table_not_identity():
    result = classify_message("Создай таблицу AI сервисов", {})
    assert result["intent"] != "identity", f"should not be identity, got {result}"
    assert result["intent"] == "table"


def test_classify_research_not_identity():
    result = classify_message("найди топ LLM провайдеров", {})
    assert result["intent"] != "identity", f"should not be identity, got {result}"


def test_classify_engineer_not_identity():
    result = classify_message("улучши архитектуру этого кода", {})
    assert result["intent"] != "identity", f"should not be identity, got {result}"


# ---------------------------------------------------------------------------
# identity_answer() — must contain Jarvis markers, no leaked names
# ---------------------------------------------------------------------------

def test_identity_answer_contains_jarvis_name():
    answer = identity_answer()
    assert JARVIS_NAME in answer, f"JARVIS_NAME missing from identity_answer()"


def test_identity_answer_contains_version():
    answer = identity_answer()
    assert JARVIS_VERSION in answer, f"JARVIS_VERSION missing from identity_answer()"


def test_identity_answer_contains_owner():
    answer = identity_answer()
    assert JARVIS_OWNER in answer, f"JARVIS_OWNER missing from identity_answer()"


def test_identity_answer_no_perplexity_self_id():
    # "Я НЕ Perplexity" is intentional; must NOT claim to be Perplexity
    answer = identity_answer().lower()
    assert "я perplexity" not in answer
    assert "i am perplexity" not in answer


def test_identity_answer_no_claude():
    answer = identity_answer().lower()
    assert "claude" not in answer, "identity_answer() must not mention Claude"


def test_identity_answer_no_luxify_self_id():
    # "Я НЕ Luxify" is intentional; must NOT claim to be Luxify
    answer = identity_answer().lower()
    assert "я luxify" not in answer
    assert "i am luxify" not in answer


# ---------------------------------------------------------------------------
# Constants are in sync with expected values
# ---------------------------------------------------------------------------

def test_constants_values():
    assert JARVIS_NAME == "Jarvis V3 Supervisor"
    assert JARVIS_OWNER == "Daniil"
    assert JARVIS_VERSION == "3.0"


# ---------------------------------------------------------------------------
# Phase 6: Capability Registry — classify_message routes
# ---------------------------------------------------------------------------

def test_classify_ai_agents_question_is_capabilities():
    result = classify_message("С какими AI агентами работаешь?", {})
    assert result["intent"] == "capabilities", f"expected capabilities, got {result}"


def test_classify_content_question_is_capabilities():
    result = classify_message("Какой контент создаёшь?", {})
    assert result["intent"] == "capabilities", f"expected capabilities, got {result}"


def test_classify_integrations_question_is_capabilities():
    result = classify_message("Какие интеграции?", {})
    assert result["intent"] == "capabilities", f"expected capabilities, got {result}"


def test_classify_ai_agents_not_identity():
    result = classify_message("С какими AI агентами работаешь?", {})
    assert result["intent"] != "identity", "AI agents question must not be routed to identity"


def test_classify_rabotaesh_with_api_not_identity():
    result = classify_message("работаешь с этим API?", {})
    assert result["intent"] != "identity", "mid-sentence 'работаешь' must not trigger identity"


def test_classify_ty_rabotaesh_routes_to_self_status():
    # H9.2: "ты работаешь?" is a status check, not an identity question
    result = classify_message("ты работаешь?", {})
    assert result["intent"] == "self_status", f"expected self_status, got {result['intent']}"


def test_classify_kakie_instrumenty_is_capabilities():
    result = classify_message("какие инструменты есть?", {})
    assert result["intent"] == "capabilities", f"expected capabilities, got {result}"


# ---------------------------------------------------------------------------
# Phase 6: capabilities_text() content from CAPABILITY_REGISTRY
# ---------------------------------------------------------------------------

def test_capabilities_text_contains_openai():
    assert "OpenAI" in capabilities_text()


def test_capabilities_text_contains_anthropic():
    assert "Anthropic" in capabilities_text()


def test_capabilities_text_contains_telegram():
    assert "Telegram" in capabilities_text()


def test_capabilities_text_contains_excel():
    assert "Excel" in capabilities_text()


def test_capabilities_text_marks_ollama_partial():
    text = capabilities_text()
    partial_ids = [c["id"] for c in CAPABILITIES if c["status"] == CAPABILITY_STATUS_PARTIAL]
    assert "ollama" in partial_ids, "Ollama must be PARTIAL in CAPABILITIES registry"
    assert "⚠️" in text, "capabilities_text() must show ⚠️ for PARTIAL entries"


def test_capabilities_text_contains_perplexity():
    assert "Perplexity" in capabilities_text()


def test_capabilities_registry_has_17_entries():
    # H6.4 added photo_studio (8) + autonomy (4) entries — registry now has 29
    assert len(CAPABILITIES) >= 17


def test_identity_answer_is_shorter_than_capabilities():
    assert len(identity_answer()) < len(capabilities_text()), \
        "identity_answer should be shorter than full capabilities listing"


def test_identity_answer_points_to_capabilities():
    answer = identity_answer().lower()
    assert "что ты умеешь" in answer or "умеешь" in answer, \
        "identity_answer must point user to the capabilities command"


# ---------------------------------------------------------------------------
# Phase 7.B: Honest Fallback — http_json returns error dict, never raises
# ---------------------------------------------------------------------------

def test_http_json_returns_error_dict_on_connection_refused():
    """http_json must return {"ok": False, "_error": ...} when backend is unreachable."""
    import urllib.error
    from unittest.mock import patch
    from tools.jarvis_smart_telegram_control import http_json

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")):
        result = http_json("GET", "http://127.0.0.1:9999/health")

    assert result.get("ok") is False, "ok must be False on connection error"
    assert "_error" in result, "_error key must be present"
    assert "Connection refused" in result["_error"]


def test_http_json_returns_error_dict_on_timeout():
    """http_json must return {"ok": False, "_error": ...} on timeout, not raise."""
    from unittest.mock import patch
    from tools.jarvis_smart_telegram_control import http_json

    with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
        result = http_json("GET", "http://127.0.0.1:9999/health", timeout=1)

    assert result.get("ok") is False
    assert "_error" in result
    assert "_error_type" in result


def test_http_json_returns_error_dict_on_http_500():
    """http_json must return error dict on HTTP 500, not raise."""
    import urllib.error
    from unittest.mock import patch
    from tools.jarvis_smart_telegram_control import http_json

    err = urllib.error.HTTPError(
        url="http://127.0.0.1:8015/api", code=500,
        msg="Internal Server Error", hdrs=None, fp=None,  # type: ignore[arg-type]
    )
    with patch("urllib.request.urlopen", side_effect=err):
        result = http_json("POST", "http://127.0.0.1:8015/api", {"q": "test"})

    assert result.get("ok") is False
    assert "_error" in result


# ---------------------------------------------------------------------------
# Phase 7.B: Table intent sends honest error when backend returns _error
# ---------------------------------------------------------------------------

def _make_state() -> dict:
    return {
        "mode": "auto",
        "language": "ru",
        "table_language": "ru",
        "keep_names_original": True,
        "last_topic": "",
        "last_table_query": "",
        "last_table_path": "",
        "pending": None,
        "preferences": {
            "answer_language": "ru",
            "tables_language": "ru",
            "names_original": True,
            "short_status": True,
        },
    }


def test_table_intent_sends_error_message_on_backend_error(monkeypatch):
    """When backend_post returns {_error: ...}, user must see ❌ error message."""
    import tools.jarvis_smart_telegram_control as mod

    sent: list[str] = []
    # send_and_get_id is used for progress messages, send for final error
    monkeypatch.setattr(mod, "send", lambda chat_id, text, **kw: sent.append(text))
    monkeypatch.setattr(mod, "send_and_get_id", lambda chat_id, text: None)
    monkeypatch.setattr(mod, "edit_message", lambda chat_id, msg_id, text: None)
    monkeypatch.setattr(mod, "backend_post",
                        lambda path, payload, timeout=180:
                        {"ok": False, "_error": "[WinError 10061] Connection refused"})
    monkeypatch.setattr(mod, "save_state", lambda s: None)

    # /table is PAID → money-gated; token simulates post-confirm so the
    # execution path under test runs (gating covered in test_money_confirm_gate).
    mod.run_intent("123", {"intent": "table", "query": "AI сервисы"}, {**_make_state(), "_paid_confirmed": "/table"})

    assert len(sent) >= 1, f"Expected at least 1 error message, got {len(sent)}: {sent}"
    assert any("❌" in m for m in sent), f"Must contain ❌ error message, got: {sent}"
    assert any("Не удалось создать таблицу" in m for m in sent)
    assert any("WinError" in m or "Connection refused" in m for m in sent)


def test_table_intent_sends_success_message_on_ok_response(monkeypatch):
    """When backend_post returns ok data, user must see ✅ message."""
    import tools.jarvis_smart_telegram_control as mod

    sent: list[str] = []
    monkeypatch.setattr(mod, "send", lambda chat_id, text: sent.append(text))
    monkeypatch.setattr(mod, "backend_post",
                        lambda path, payload, timeout=180: {
                            "ok": True,
                            "rows_count": 10,
                            "table_path": "/tmp/table.xlsx",
                            "telegram_send": {"ok": True},
                        })
    monkeypatch.setattr(mod, "save_state", lambda s: None)

    # /table is PAID → money-gated; token simulates post-confirm so the
    # execution path under test runs (gating covered in test_money_confirm_gate).
    mod.run_intent("123", {"intent": "table", "query": "AI сервисы"}, {**_make_state(), "_paid_confirmed": "/table"})

    assert any("✅" in m for m in sent), f"Expected ✅ in messages, got: {sent}"
    assert not any("❌" in m for m in sent), f"Got unexpected ❌: {sent}"


def test_research_intent_sends_error_on_backend_error(monkeypatch):
    """research intent must send error message when backend is down."""
    import tools.jarvis_smart_telegram_control as mod

    sent: list[str] = []
    monkeypatch.setattr(mod, "send", lambda chat_id, text: sent.append(text))
    monkeypatch.setattr(mod, "backend_post",
                        lambda path, payload, timeout=180:
                        {"ok": False, "_error": "URLError: timeout"})
    monkeypatch.setattr(mod, "save_state", lambda s: None)

    # /research is PAID → money-gated; token simulates post-confirm.
    mod.run_intent("123", {"intent": "research", "query": "топ AI сервисы"}, {**_make_state(), "_paid_confirmed": "/research"})

    assert any("❌" in m for m in sent), f"Expected error message, got: {sent}"
    assert any("Не смог получить результат" in m for m in sent)


def test_chat_intent_sends_error_on_backend_error(monkeypatch):
    """chat intent must send error message when backend is down."""
    import tools.jarvis_smart_telegram_control as mod

    sent: list[str] = []
    monkeypatch.setattr(mod, "send", lambda chat_id, text: sent.append(text))
    monkeypatch.setattr(mod, "backend_post",
                        lambda path, payload, timeout=180:
                        {"ok": False, "_error": "Connection refused"})
    monkeypatch.setattr(mod, "save_state", lambda s: None)

    mod.run_intent("123", {"intent": "chat", "query": "Привет"}, _make_state())

    assert any("❌" in m for m in sent), f"Expected error message, got: {sent}"


# ---------------------------------------------------------------------------
# Phase 7.5.0: ENV fallback chain tests
# ---------------------------------------------------------------------------

def test_backend_env_fallback_uses_backend_base_url(monkeypatch):
    """BACKEND_BASE_URL takes priority in fallback chain."""
    monkeypatch.setenv("BACKEND_BASE_URL", "http://127.0.0.1:9999")
    monkeypatch.delenv("TELEGRAM_BACKEND_URL", raising=False)
    import importlib
    m = importlib.reload(_mod)
    assert m.BACKEND == "http://127.0.0.1:9999", f"Got: {m.BACKEND}"


def test_backend_env_fallback_uses_telegram_backend_url(monkeypatch):
    """TELEGRAM_BACKEND_URL used when BACKEND_BASE_URL is absent."""
    monkeypatch.delenv("BACKEND_BASE_URL", raising=False)
    monkeypatch.setenv("TELEGRAM_BACKEND_URL", "http://127.0.0.1:8010")
    import importlib
    m = importlib.reload(_mod)
    assert m.BACKEND == "http://127.0.0.1:8010", f"Got: {m.BACKEND}"


def test_backend_env_fallback_default(monkeypatch):
    """Default fallback is http://127.0.0.1:8010 when both env vars are absent."""
    monkeypatch.delenv("BACKEND_BASE_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_BACKEND_URL", raising=False)
    import importlib
    m = importlib.reload(_mod)
    assert m.BACKEND == "http://127.0.0.1:8010", f"Got: {m.BACKEND}"
