# -*- coding: utf-8 -*-
"""IR-1 wiring into the bot: classify_message tail + run_intent + callback.

$0, mocks only, no network. Proves the router routes residual free text AND
that existing intents/FSMs are NOT regressed (invariant: only the former
`chat` catch-all changes).
"""
import tools.jarvis_smart_telegram_control as mod


# ── Task 5: classify_message routes residual free text (admin) ─────────────
def test_classify_routes_bot_health_phrase_to_ir():
    pack = mod.classify_message("глянь что с ботом", {})
    assert pack["intent"] == "ir_route"
    assert pack["command"] == "/health"


def test_classify_routes_spending_phrase_to_ir():
    pack = mod.classify_message("что я потратил сегодня", {})
    assert pack["intent"] == "ir_route"
    assert pack["command"] in ("/costs", "/my_stats")


def test_classify_unknown_free_text_is_ir_unknown():
    pack = mod.classify_message("асдфгхйцукен блаблабла", {})
    assert pack["intent"] == "ir_unknown"


# ── Sentinels: existing intents must NOT regress (invariant) ───────────────
def test_sentinel_health_trigger_preserved():
    assert mod.classify_message("проверить системы", {})["intent"] == "health"


def test_sentinel_greeting_preserved():
    assert mod.classify_message("привет", {})["intent"] == "greeting"


def test_sentinel_capabilities_preserved():
    assert mod.classify_message("что ты умеешь", {})["intent"] == "capabilities"


def test_sentinel_generate_trigger_still_wins_over_ir():
    # broad existing trigger catches this BEFORE the IR tail — IR must not hijack
    assert mod.classify_message("сделай фото блюда для меню", {})["intent"] == "generate"


def test_sentinel_slash_command_untouched():
    pack = mod.classify_message("/health", {})
    assert pack["intent"] == "command" and pack["command"] == "/health"
