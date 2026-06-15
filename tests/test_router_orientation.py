# -*- coding: utf-8 -*-
"""Orientation tests for the Phase-4 LLM router.

These lock in the *richer* system prompt and the *informative* tool
descriptions added so Claude orients itself well: it knows it is Jarvis, knows
the four real capabilities (and their nuances), honestly reports that persona
photo generation is still a stub, and asks to clarify rather than guessing a
tool on nonsense input. The router's routing logic is NOT exercised here — only
the static orientation surface (prompt text + tool definitions).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.unified.llm_router.router import _DEFAULT_SYSTEM_PROMPT
from app.services.unified.llm_router.tool_registry import ToolRegistry
from app.services.unified.llm_router.tools import register_default_tools


def _default_registry() -> ToolRegistry:
    reg = ToolRegistry()
    register_default_tools(
        reg,
        dispatch_fn=lambda *a, **k: None,
        set_quality_fn=lambda *a, **k: None,
        stats_fn=lambda *a, **k: "stats",
    )
    return reg


# ── system prompt: identity ──────────────────────────────────────────────────


def test_system_prompt_introduces_jarvis_persona():
    p = _DEFAULT_SYSTEM_PROMPT
    low = p.lower()
    assert "jarvis" in low
    # The owner is named so Claude knows whose assistant it is.
    assert "daniil" in low or "даниил" in low


def test_system_prompt_describes_real_capabilities():
    low = _DEFAULT_SYSTEM_PROMPT.lower()
    # face-swap
    assert "face" in low or "свап" in low or "лиц" in low
    # AI personas
    assert "персон" in low
    # animation
    assert "анимац" in low
    # batch nuance
    assert "пакет" in low or "batch" in low


def test_system_prompt_mentions_animation_nuances():
    low = _DEFAULT_SYSTEM_PROMPT.lower()
    assert "fps" in low or "кадр" in low
    assert "секунд" in low or "длительн" in low


def test_system_prompt_is_honest_about_persona_photo_stub():
    low = _DEFAULT_SYSTEM_PROMPT.lower()
    # Mentions persona photo generation...
    assert "фото персон" in low or "generate_persona_photo" in low
    # ...and honestly flags it as not yet wired.
    assert any(k in low for k in ("не подключ", "заглушк", "ещё не", "пока не"))


def test_system_prompt_has_clarify_not_guess_rule():
    low = _DEFAULT_SYSTEM_PROMPT.lower()
    # Ask to clarify...
    assert "уточн" in low
    # ...rather than guessing a tool.
    assert any(k in low for k in ("не угад", "не выдум", "не предполаг", "не додум"))


def test_system_prompt_sets_tone():
    low = _DEFAULT_SYSTEM_PROMPT.lower()
    assert "кратк" in low  # brief
    assert "русск" in low  # Russian


def test_system_prompt_substantially_richer_than_stub():
    # The original stub prompt was ~330 chars; the enriched one is much longer.
    assert len(_DEFAULT_SYSTEM_PROMPT) > 600


def test_system_prompt_mentions_video_face_swap():
    # The live-verified "replace a face in a whole video" capability is named
    # as a real tool...
    low = _DEFAULT_SYSTEM_PROMPT.lower()
    assert "video_face_swap" in low
    # ...and distinguished from photo→video animation: it needs a READY video
    # file as the source ("готовое видео").
    assert "готов" in low


def test_system_prompt_is_honest_about_voice():
    # Voice notes are transcribed/spoken outside the router; the prompt mentions
    # the capability honestly so Claude neither ignores it nor invents a tool.
    low = _DEFAULT_SYSTEM_PROMPT.lower()
    assert "голос" in low
    assert "whisper" in low or "распозна" in low


# ── tool descriptions: informative + with usage cues ─────────────────────────


def test_all_default_tools_have_rich_descriptions():
    reg = _default_registry()
    tools = reg.all()
    assert tools, "default registry must register tools"
    for t in tools:
        assert t.description and isinstance(t.description, str)
        assert len(t.description) >= 60, (
            f"{t.name} description too thin: {t.description!r}"
        )


def test_all_default_tools_have_usage_cue():
    reg = _default_registry()
    for t in reg.all():
        low = t.description.lower()
        assert any(cue in low for cue in ("когда", "например", "пример")), (
            f"{t.name} description has no 'when to use' cue: {t.description!r}"
        )


def test_persona_photo_description_is_honest_about_stub():
    reg = _default_registry()
    desc = reg.get("generate_persona_photo").description.lower()
    assert any(k in desc for k in ("не подключ", "заглушк", "ещё не", "пока не"))


def test_animation_description_mentions_fps_and_duration():
    reg = _default_registry()
    desc = reg.get("swap_batch_run_animation").description.lower()
    assert "fps" in desc or "кадр" in desc
    assert "секунд" in desc or "длительн" in desc


def test_swap_tools_keep_legacy_command_equivalents():
    # Backward-compat: descriptions still name the /swapbatch_* equivalents so
    # the mapping stays discoverable.
    reg = _default_registry()
    assert "/swapbatch_source" in reg.get("swap_batch_start_source").description
    assert "/swapbatch_go" in reg.get("swap_batch_run_swap").description


def test_stats_description_explains_when_to_use():
    reg = _default_registry()
    desc = reg.get("get_user_stats").description.lower()
    assert "расход" in desc or "статистик" in desc or "трат" in desc
