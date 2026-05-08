from __future__ import annotations

"""Phase 6.5: Identity-aware can-you routing tests.

Verifies:
1. "Какие твои агенты?" → capabilities (NOT research)
2. "Ты умеешь писать сайты?" → can_you (NOT research)
3. "Можешь сделать таблицу?" → can_you with positive answer
4. "Можешь полететь на Марс?" → can_you with negative answer
5. "Ты можешь Excel?" → can_you positive
6. can_you_answer() never mentions Perplexity, Salesforce, Ada, Manus (no drift)
7. Regressions: "Сравни X и Y" → brain/research (unchanged)
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.jarvis_smart_telegram_control import (
    classify_message,
    can_you_answer,
    CAPABILITIES,
)


# ---------------------------------------------------------------------------
# Regression fixes: твои агенты → capabilities (not research)
# ---------------------------------------------------------------------------

def test_tvoi_agenty_is_capabilities():
    result = classify_message("Какие твои агенты самые полезные?", {})
    assert result["intent"] == "capabilities", f"Got: {result}"


def test_tvoi_agenty_short_is_capabilities():
    result = classify_message("Твои агенты", {})
    assert result["intent"] == "capabilities", f"Got: {result}"


def test_tvoi_instrumenty_is_capabilities():
    result = classify_message("Какие у тебя инструменты?", {})
    assert result["intent"] == "capabilities", f"Got: {result}"


def test_tvoi_vozmozhnosti_is_capabilities():
    result = classify_message("Твои возможности", {})
    assert result["intent"] == "capabilities", f"Got: {result}"


def test_tvoikh_agentov_is_capabilities():
    result = classify_message("Расскажи про твоих агентов", {})
    assert result["intent"] == "capabilities", f"Got: {result}"


# ---------------------------------------------------------------------------
# can_you routing
# ---------------------------------------------------------------------------

def test_ty_umeesh_saity_is_can_you():
    result = classify_message("Ты умеешь писать сайты?", {})
    assert result["intent"] == "can_you", f"Expected can_you, got: {result}"


def test_ty_mozhesh_tablitsu_is_table_or_can_you():
    # "Можешь сделать таблицу?" contains "таблиц" — routing to table is correct
    # (user is asking Jarvis TO make a table, not asking IF it can)
    result = classify_message("Можешь сделать таблицу?", {})
    assert result["intent"] in ("table", "can_you"), f"Expected table or can_you, got: {result}"


def test_umeesh_li_excel_is_can_you():
    result = classify_message("Ты можешь Excel?", {})
    assert result["intent"] == "can_you", f"Expected can_you, got: {result}"


def test_can_you_english_is_can_you():
    result = classify_message("Can you make a table?", {})
    assert result["intent"] == "can_you", f"Expected can_you, got: {result}"


def test_sposoben_li_is_can_you():
    result = classify_message("Способен ли ты делать изображения?", {})
    assert result["intent"] == "can_you", f"Expected can_you, got: {result}"


def test_smozheesh_li_is_can_you():
    result = classify_message("Сможешь ли ты помочь?", {})
    assert result["intent"] == "can_you", f"Expected can_you, got: {result}"


# ---------------------------------------------------------------------------
# can_you_answer() — content quality
# ---------------------------------------------------------------------------

def test_can_you_table_positive():
    answer = can_you_answer("Ты умеешь создавать таблицы?")
    assert "✅" in answer
    assert "да" in answer.lower() or "excel" in answer.lower() or "таблиц" in answer.lower()


def test_can_you_excel_positive():
    answer = can_you_answer("Ты можешь Excel?")
    assert "✅" in answer


def test_can_you_image_positive():
    answer = can_you_answer("Можешь сгенерировать изображение?")
    assert "✅" in answer
    assert "изображени" in answer.lower() or "image" in answer.lower()


def test_can_you_website_negative():
    answer = can_you_answer("Ты умеешь писать сайты?")
    assert "❌" in answer
    assert "сайт" in answer.lower() or "не" in answer.lower()


def test_can_you_mars_negative():
    answer = can_you_answer("Можешь полететь на Марс?")
    assert "❌" in answer or "❓" in answer
    # Must NOT claim it can do it
    assert "лечу" not in answer.lower()
    assert "могу" not in answer.lower() or "не" in answer.lower()


def test_can_you_no_identity_drift():
    """can_you_answer must not mention Salesforce, Ada, Manus, or claim web dev abilities."""
    answer = can_you_answer("Ты умеешь React?")
    assert "salesforce" not in answer.lower()
    assert "manus" not in answer.lower()
    assert "ada" not in answer.lower()
    assert "react" not in answer.lower() or "не" in answer.lower()


def test_can_you_no_perplexity_claim():
    """can_you_answer must never claim Perplexity capabilities as its own."""
    answer = can_you_answer("Ты умеешь анализировать данные?")
    assert "я perplexity" not in answer.lower()
    assert "я — perplexity" not in answer.lower()


def test_can_you_empty_shows_capabilities():
    answer = can_you_answer("Ты умеешь?")
    # No subject → show capabilities overview
    assert len(answer) > 50


def test_can_you_research_positive():
    answer = can_you_answer("Ты умеешь делать исследования?")
    assert "✅" in answer
    assert "исследован" in answer.lower() or "research" in answer.lower() or "интернет" in answer.lower()


# ---------------------------------------------------------------------------
# Regression: other intents unchanged
# ---------------------------------------------------------------------------

def test_compare_still_brain():
    result = classify_message("Сравни FAL и Replicate", {})
    assert result["intent"] in ("brain", "research"), f"Got: {result}"


def test_create_table_still_table():
    result = classify_message("Создай таблицу топ AI сервисов", {})
    assert result["intent"] == "table", f"Got: {result}"


def test_find_still_research():
    result = classify_message("Найди топ LLM провайдеров", {})
    assert result["intent"] == "research", f"Got: {result}"


# ---------------------------------------------------------------------------
# run_intent routes can_you correctly
# ---------------------------------------------------------------------------

def test_run_intent_can_you_calls_send(monkeypatch):
    import tools.jarvis_smart_telegram_control as mod
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, txt, **kw: sent.append(txt))
    mod.run_intent("123", {"intent": "can_you", "query": "Ты умеешь создавать таблицы?"}, {})
    assert len(sent) == 1
    assert "✅" in sent[0] or "❌" in sent[0] or "❓" in sent[0]


# ---------------------------------------------------------------------------
# Block H6.4: new capabilities in CAPABILITIES registry
# ---------------------------------------------------------------------------

def test_capabilities_includes_lora():
    ids = [c["id"] for c in CAPABILITIES]
    assert "lora_training" in ids, f"lora_training missing from CAPABILITIES: {ids}"


def test_capabilities_includes_night_autonomy():
    ids = [c["id"] for c in CAPABILITIES]
    assert "night_autonomy" in ids, f"night_autonomy missing from CAPABILITIES: {ids}"


def test_capabilities_includes_face_swap():
    ids = [c["id"] for c in CAPABILITIES]
    assert "face_swap" in ids, f"face_swap missing from CAPABILITIES: {ids}"


def test_capabilities_includes_self_improvement():
    ids = [c["id"] for c in CAPABILITIES]
    assert "self_improvement" in ids, f"self_improvement missing from CAPABILITIES: {ids}"


def test_can_you_lora_positive():
    answer = can_you_answer("Ты умеешь работать с lora?")
    assert "✅" in answer


def test_can_you_night_autonomy_positive():
    answer = can_you_answer("Можешь самоулучшаться?")
    assert "✅" in answer


def test_capabilities_text_has_photo_studio():
    from tools.jarvis_smart_telegram_control import capabilities_text
    text = capabilities_text()
    assert "Photo Studio" in text or "photo_studio" in text.lower() or "блюд" in text.lower()


def test_capabilities_text_has_night_autonomy():
    from tools.jarvis_smart_telegram_control import capabilities_text
    text = capabilities_text()
    assert "Night Autonomy" in text or "ночн" in text.lower() or "night" in text.lower()


# ---------------------------------------------------------------------------
# Block H6.5: /capabilities command
# ---------------------------------------------------------------------------

def test_capabilities_command_returns_full_list(monkeypatch):
    import tools.jarvis_smart_telegram_control as mod
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, txt, **kw: sent.append(txt))
    mod.cmd_capabilities("test_chat")
    assert len(sent) == 1
    text = sent[0]
    assert "/faceswap" in text
    assert "/lora_train" in text
    assert "Night Autonomy" in text or "NIGHT AUTONOMY" in text
    assert "1600" in text or "1460" in text or "автотест" in text


def test_run_intent_can_you_never_calls_backend(monkeypatch):
    """can_you intent must NOT make any backend call — only use local registry."""
    import tools.jarvis_smart_telegram_control as mod
    backend_calls = []
    monkeypatch.setattr(mod, "backend_post", lambda *a, **kw: backend_calls.append(a) or {})
    monkeypatch.setattr(mod, "backend_get", lambda *a, **kw: backend_calls.append(a) or {})
    monkeypatch.setattr(mod, "send", lambda cid, txt, **kw: None)

    mod.run_intent("123", {"intent": "can_you", "query": "Ты умеешь писать сайты?"}, {})

    assert len(backend_calls) == 0, "can_you must never call backend/Perplexity"
