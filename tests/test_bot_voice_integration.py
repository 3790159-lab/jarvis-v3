# -*- coding: utf-8 -*-
"""Integration: a Telegram voice note is transcribed and fed into the normal
text flow; an optional spoken reply is sent only when the feature flag is on.

Same fresh-module-load harness as ``tests/test_bot_router_integration``. The
unified voice layer is faked at the bot seams (``_voice_transcribe`` /
``_voice_synthesize`` / ``_send_voice_note``) so no OpenAI/ElevenLabs call is
made. Backward compatibility is the contract: plain text is never sent through
transcription, and voice replies stay off unless ``JARVIS_VOICE_REPLY_ENABLED=1``.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.unified.llm_router.router import RouterResponse
from app.services.unified.voice.synthesize import SynthesisResult
from app.services.unified.voice.transcribe import TranscriptionResult


def _get_mod():
    mod_name = f"_test_voice_bot_{id(object())}"
    spec = importlib.util.spec_from_file_location(
        mod_name, ROOT / "tools" / "jarvis_smart_telegram_control.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _voice_update(user_id: int, duration: int = 3) -> dict:
    msg = {
        "from": {"id": user_id, "username": "tester"},
        "chat": {"id": user_id},
        "voice": {"file_id": "voice_file_1", "duration": duration},
    }
    return {"update_id": 1, "message": msg}


def _text_update(user_id: int, text: str) -> dict:
    msg = {"from": {"id": user_id, "username": "tester"}, "chat": {"id": user_id}, "text": text}
    return {"update_id": 1, "message": msg}


class _FakeRouter:
    def __init__(self, response: RouterResponse) -> None:
        self.response = response
        self.calls: list = []

    async def route_message(self, text, context, conversation_history=None):
        self.calls.append((text, context))
        return self.response


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_ALLOWED_USER_IDS", "222")
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", "111")
    monkeypatch.setenv("JARVIS_AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.setenv("JARVIS_COST_FILE", str(tmp_path / "cost.json"))
    monkeypatch.setenv("JARVIS_ROUTER_ENABLED", "0")
    monkeypatch.setenv("JARVIS_VOICE_REPLY_ENABLED", "0")


def _wire_common(mod, monkeypatch, sent):
    monkeypatch.setattr(mod, "_download_telegram_file", lambda fid, name: "/tmp/voice.oga")
    monkeypatch.setattr(mod, "send", lambda cid, txt, *a, **k: sent.append((str(cid), txt)))


def test_voice_note_transcribed_and_routed_to_legacy(monkeypatch):
    mod = _get_mod()
    sent: list = []
    handle_calls: list = []
    transcribe_calls: list = []
    _wire_common(mod, monkeypatch, sent)

    def fake_transcribe(path, *, duration_sec=None):
        transcribe_calls.append((path, duration_sec))
        return TranscriptionResult(text="сделай swap этих фото", cost_usd=0.0003, duration_sec=duration_sec or 0)

    monkeypatch.setattr(mod, "_voice_transcribe", fake_transcribe)
    monkeypatch.setattr(mod, "handle", lambda cid, txt: handle_calls.append((str(cid), txt)))

    mod.process_update(_voice_update(222, duration=30))

    # Transcription ran, with the voice note's duration forwarded for cost.
    assert transcribe_calls == [("/tmp/voice.oga", 30)]
    # The transcribed text reached the legacy dispatcher (router disabled).
    assert handle_calls == [("222", "сделай swap этих фото")]
    # The user saw the recognised text.
    assert any("сделай swap этих фото" in t for _, t in sent)


def test_voice_note_routes_through_llm_when_enabled(monkeypatch):
    monkeypatch.setenv("JARVIS_ROUTER_ENABLED", "1")
    mod = _get_mod()
    sent: list = []
    _wire_common(mod, monkeypatch, sent)
    fake = _FakeRouter(RouterResponse(text="Готово!"))

    monkeypatch.setattr(
        mod, "_voice_transcribe",
        lambda path, *, duration_sec=None: TranscriptionResult(text="привет джарвис"),
    )
    monkeypatch.setattr(mod, "_build_router", lambda: fake)
    monkeypatch.setattr(mod, "handle", lambda cid, txt: pytest.fail("legacy handle must not run"))

    mod.process_update(_voice_update(222))

    assert fake.calls and fake.calls[0][0] == "привет джарвис"
    assert any("Готово!" in t for _, t in sent)


def test_voice_reply_sent_when_enabled(monkeypatch):
    monkeypatch.setenv("JARVIS_ROUTER_ENABLED", "1")
    monkeypatch.setenv("JARVIS_VOICE_REPLY_ENABLED", "1")
    mod = _get_mod()
    sent: list = []
    voice_notes: list = []
    _wire_common(mod, monkeypatch, sent)
    fake = _FakeRouter(RouterResponse(text="Ответ голосом"))

    monkeypatch.setattr(
        mod, "_voice_transcribe",
        lambda path, *, duration_sec=None: TranscriptionResult(text="скажи голосом"),
    )
    monkeypatch.setattr(mod, "_build_router", lambda: fake)
    monkeypatch.setattr(
        mod, "_voice_synthesize",
        lambda text: SynthesisResult(audio=b"OGG-AUDIO", audio_format="ogg", provider="openai", cost_usd=0.0001),
    )
    monkeypatch.setattr(
        mod, "_send_voice_note",
        lambda cid, audio, audio_format="ogg": voice_notes.append((str(cid), audio, audio_format)),
    )

    mod.process_update(_voice_update(222))

    assert voice_notes == [("222", b"OGG-AUDIO", "ogg")]


def test_voice_reply_not_sent_when_disabled(monkeypatch):
    monkeypatch.setenv("JARVIS_ROUTER_ENABLED", "1")
    # JARVIS_VOICE_REPLY_ENABLED stays 0 (default from fixture).
    mod = _get_mod()
    sent: list = []
    voice_notes: list = []
    _wire_common(mod, monkeypatch, sent)
    fake = _FakeRouter(RouterResponse(text="Ответ текстом"))

    monkeypatch.setattr(
        mod, "_voice_transcribe",
        lambda path, *, duration_sec=None: TranscriptionResult(text="ответь"),
    )
    monkeypatch.setattr(mod, "_build_router", lambda: fake)
    monkeypatch.setattr(
        mod, "_voice_synthesize",
        lambda text: pytest.fail("synthesis must not run when voice reply disabled"),
    )
    monkeypatch.setattr(mod, "_send_voice_note", lambda *a, **k: voice_notes.append(a))

    mod.process_update(_voice_update(222))

    assert voice_notes == []
    assert any("Ответ текстом" in t for _, t in sent)


def test_transcription_failure_is_graceful(monkeypatch):
    mod = _get_mod()
    sent: list = []
    handle_calls: list = []
    _wire_common(mod, monkeypatch, sent)

    monkeypatch.setattr(
        mod, "_voice_transcribe",
        lambda path, *, duration_sec=None: TranscriptionResult(error="OPENAI_API_KEY не задан."),
    )
    monkeypatch.setattr(mod, "handle", lambda cid, txt: handle_calls.append((str(cid), txt)))

    mod.process_update(_voice_update(222))

    # Error surfaced to the user; nothing routed to the dispatcher.
    assert handle_calls == []
    assert any("OPENAI_API_KEY" in t for _, t in sent)


def test_plain_text_does_not_trigger_transcription(monkeypatch):
    mod = _get_mod()
    sent: list = []
    handle_calls: list = []
    _wire_common(mod, monkeypatch, sent)

    monkeypatch.setattr(
        mod, "_voice_transcribe",
        lambda *a, **k: pytest.fail("text messages must not be transcribed"),
    )
    monkeypatch.setattr(mod, "handle", lambda cid, txt: handle_calls.append((str(cid), txt)))

    mod.process_update(_text_update(222, "обычный текст"))

    assert handle_calls == [("222", "обычный текст")]
