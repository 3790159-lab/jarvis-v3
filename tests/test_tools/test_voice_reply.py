# -*- coding: utf-8 -*-
"""Tests for the ``reply_with_voice`` router tool.

The tool lets Claude speak a specific reply on request ("ответь голосом",
"озвучь") by reusing the existing ``synthesize_speech`` TTS pipeline. It works
independently of the global ``JARVIS_VOICE_REPLY_ENABLED`` flag (the flag voices
*every* reply; the tool voices *this* reply because the user asked). Synthesis
failures (e.g. a missing/invalid OpenAI key) surface as an honest tool error —
never a silent success.
"""
from __future__ import annotations

import asyncio

from app.services.unified.llm_router.tool_registry import ToolContext, ToolRegistry
from app.services.unified.llm_router.tools import register_default_tools
from app.services.unified.llm_router.tools.voice_reply import build_voice_reply_tool


def _ctx() -> ToolContext:
    return ToolContext(user_id=1, username="u", chat_id="555", conversation_state={})


class _FakeResult:
    """Stand-in for ``SynthesisResult``."""

    def __init__(self, audio=b"AUDIO", audio_format="ogg", cost_usd=0.01, provider="openai", error=""):
        self.audio = audio
        self.audio_format = audio_format
        self.cost_usd = cost_usd
        self.provider = provider
        self.error = error

    @property
    def is_error(self) -> bool:
        return bool(self.error)


# ── tool definition ──────────────────────────────────────────────────────────


def test_tool_name_and_rich_description():
    tool = build_voice_reply_tool()
    assert tool.name == "reply_with_voice"
    assert tool.description and len(tool.description) >= 60
    low = tool.description.lower()
    assert any(cue in low for cue in ("когда", "например", "пример"))
    assert "голос" in low or "озвуч" in low
    # The text-to-speak is a required input.
    assert tool.input_schema.get("required") == ["text"]


# ── happy path ───────────────────────────────────────────────────────────────


def test_synthesizes_and_sends():
    synth_calls = []
    send_calls = []

    def synth(text):
        synth_calls.append(text)
        return _FakeResult()

    def send(context, result):
        send_calls.append((context.chat_id, result.audio, result.audio_format))

    tool = build_voice_reply_tool(synthesize_fn=synth, send_fn=send)
    result = asyncio.run(tool.handler({"text": "привет"}, _ctx()))

    assert not result.is_error
    assert synth_calls == ["привет"]
    assert send_calls == [("555", b"AUDIO", "ogg")]


# ── honest errors ────────────────────────────────────────────────────────────


def test_honest_error_when_synthesis_fails():
    send_calls = []
    tool = build_voice_reply_tool(
        synthesize_fn=lambda text: _FakeResult(audio=b"", error="OPENAI_API_KEY не задан."),
        send_fn=lambda context, result: send_calls.append(result),
    )
    result = asyncio.run(tool.handler({"text": "привет"}, _ctx()))
    assert result.is_error
    assert "openai_api_key" in result.error.lower()
    assert send_calls == []  # nothing sent on failure


def test_synth_raising_is_caught():
    def boom(text):
        raise RuntimeError("tts blew up")

    tool = build_voice_reply_tool(synthesize_fn=boom, send_fn=lambda *a: None)
    result = asyncio.run(tool.handler({"text": "привет"}, _ctx()))
    assert result.is_error
    assert "tts blew up" in result.error


def test_empty_text_fails_without_synthesizing():
    synth_calls = []
    tool = build_voice_reply_tool(
        synthesize_fn=lambda text: synth_calls.append(text) or _FakeResult(),
        send_fn=lambda *a: None,
    )
    result = asyncio.run(tool.handler({"text": "   "}, _ctx()))
    assert result.is_error
    assert synth_calls == []


def test_no_send_fn_fails_gracefully():
    tool = build_voice_reply_tool(synthesize_fn=lambda text: _FakeResult(), send_fn=None)
    result = asyncio.run(tool.handler({"text": "привет"}, _ctx()))
    assert result.is_error  # graceful, does not raise


# ── flag independence ────────────────────────────────────────────────────────


def test_works_regardless_of_global_flag(monkeypatch):
    # Global "voice every reply" flag is OFF, yet the on-request tool still speaks.
    monkeypatch.setenv("JARVIS_VOICE_REPLY_ENABLED", "0")
    send_calls = []
    tool = build_voice_reply_tool(
        synthesize_fn=lambda text: _FakeResult(),
        send_fn=lambda context, result: send_calls.append(result),
    )
    result = asyncio.run(tool.handler({"text": "привет"}, _ctx()))
    assert not result.is_error
    assert len(send_calls) == 1


# ── registration ─────────────────────────────────────────────────────────────


def test_registered_in_default_tools():
    reg = ToolRegistry()
    register_default_tools(
        reg,
        dispatch_fn=lambda *a, **k: None,
        set_quality_fn=lambda *a, **k: None,
        stats_fn=lambda *a, **k: "s",
        voice_synthesize_fn=lambda text: _FakeResult(),
        voice_send_fn=lambda *a, **k: None,
    )
    assert reg.get("reply_with_voice") is not None
