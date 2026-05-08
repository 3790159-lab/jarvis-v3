# -*- coding: utf-8 -*-
"""Tests for M.1.3.0 — PhotoGenerator and handle_persona_photo handler."""
from __future__ import annotations

import threading
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.block_m_common.cost_tracker import DailyLimitExceeded
from app.services.block_m_common.persona_storage import Persona
from app.services.block_m1_persona.photo_generator import PhotoGenerator


# ── fixtures ─────────────────────────────────────────────────────────────────

def _make_persona(lora_weights_url: str | None = "https://weights.example/lora.safetensors") -> Persona:
    return Persona(
        persona_id="persona_test01",
        name="TestBot",
        description="slim brunette",
        style="lifestyle",
        lora_weights_url=lora_weights_url,
        trigger_word="sks_testbot",
        created_at=datetime(2026, 1, 1),
        seed_photos=["https://example.com/img1.jpg"],
        total_generations=3,
        total_cost_usd=0.06,
    )


def _make_generator(
    persona: Persona | None = None,
    check_limit_ok: bool = True,
    image_url: str = "https://replicate.delivery/out/photo.jpg",
) -> tuple[PhotoGenerator, MagicMock, MagicMock, MagicMock]:
    client = MagicMock()
    storage = MagicMock()
    tracker = MagicMock()

    storage.get_persona = AsyncMock(return_value=persona if persona is not None else _make_persona())
    tracker.check_limit = AsyncMock(
        return_value=(check_limit_ok, 1.20 if not check_limit_ok else 0.50)
    )
    tracker.log_expense = AsyncMock(return_value=None)
    client.generate_flux_with_lora = AsyncMock(
        return_value={"image_url": image_url, "cost_usd": 0.02}
    )
    storage.update_persona = AsyncMock(return_value=None)

    gen = PhotoGenerator(client, storage, tracker)
    return gen, client, storage, tracker


# ── PhotoGenerator tests ──────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_generate_photo_returns_correct_dict():
    gen, _, _, _ = _make_generator()
    result = await gen.generate_photo("persona_test01", "in a park")
    assert "image_url" in result
    assert "cost_usd" in result
    assert "full_prompt" in result
    assert result["cost_usd"] == 0.02
    assert result["image_url"] == "https://replicate.delivery/out/photo.jpg"


@pytest.mark.anyio
async def test_generate_photo_builds_full_prompt():
    gen, client, _, _ = _make_generator()
    result = await gen.generate_photo("persona_test01", "in a park")
    assert result["full_prompt"] == "sks_testbot in a park"
    # verify generate_flux_with_lora received the raw user prompt (not the full_prompt)
    call_kwargs = client.generate_flux_with_lora.call_args.kwargs
    assert call_kwargs["prompt"] == "in a park"
    assert call_kwargs["trigger_word"] == "sks_testbot"


@pytest.mark.anyio
async def test_generate_photo_raises_if_persona_not_found():
    gen, _, storage, _ = _make_generator()
    storage.get_persona = AsyncMock(return_value=None)
    with pytest.raises(ValueError, match="not found"):
        await gen.generate_photo("persona_test01", "smile")


@pytest.mark.anyio
async def test_generate_photo_raises_if_lora_not_trained():
    persona = _make_persona(lora_weights_url=None)
    gen, _, _, _ = _make_generator(persona=persona)
    with pytest.raises(ValueError, match="no trained LoRA"):
        await gen.generate_photo("persona_test01", "smile")


@pytest.mark.anyio
async def test_generate_photo_raises_daily_limit_exceeded():
    gen, _, _, _ = _make_generator(check_limit_ok=False)
    with pytest.raises(DailyLimitExceeded):
        await gen.generate_photo("persona_test01", "smile")


@pytest.mark.anyio
async def test_generate_photo_logs_expense():
    gen, _, _, tracker = _make_generator()
    await gen.generate_photo("persona_test01", "on the beach")
    tracker.log_expense.assert_called_once_with("flux_lora_inference", 0.02, "persona_test01")


@pytest.mark.anyio
async def test_generate_photo_updates_persona_stats():
    gen, _, storage, _ = _make_generator()
    await gen.generate_photo("persona_test01", "on the beach")
    storage.update_persona.assert_called_once_with(
        "persona_test01",
        total_generations=4,   # was 3
        total_cost_usd=pytest.approx(0.08),  # was 0.06 + 0.02
    )


@pytest.mark.anyio
async def test_generate_photo_calls_progress_cb():
    gen, _, _, _ = _make_generator()
    cb = MagicMock()
    await gen.generate_photo("persona_test01", "posing", progress_cb=cb)
    cb.assert_called_once_with("generating")


# ── handle_persona_photo handler tests ───────────────────────────────────────

def _init_handler() -> tuple[MagicMock, MagicMock]:
    import app.handlers.persona_handler as ph
    send = MagicMock()
    send_photo = MagicMock()
    ph.init_bot(send, send_photo)
    return send, send_photo


def test_handle_persona_photo_missing_args():
    from app.handlers.persona_handler import handle_persona_photo
    send, _ = _init_handler()
    handle_persona_photo(42, "")
    send.assert_called_once()
    assert "persona_id" in send.call_args[0][1].lower() or "укажите" in send.call_args[0][1].lower()


def test_handle_persona_photo_missing_prompt():
    from app.handlers.persona_handler import handle_persona_photo
    send, _ = _init_handler()
    handle_persona_photo(42, "persona_test01")
    send.assert_called_once()
    assert "промпт" in send.call_args[0][1].lower()


def _fake_start_sync(completed: threading.Event):
    """Return a fake Thread.start that runs target synchronously then sets the event."""
    def _start(self):
        self._target()
        completed.set()
    return _start


def _handler_infra_patches(generate_photo_mock):
    """Patch out infra constructors so the handler thread doesn't need env vars."""
    return [
        patch("app.handlers.persona_handler.ReplicateVideoClient", MagicMock()),
        patch("app.handlers.persona_handler.PersonaStorage", MagicMock()),
        patch("app.handlers.persona_handler.CostTracker", MagicMock()),
        patch(
            "app.services.block_m1_persona.photo_generator.PhotoGenerator.generate_photo",
            new=generate_photo_mock,
        ),
    ]


def test_handle_persona_photo_persona_not_found():
    from app.handlers.persona_handler import handle_persona_photo
    from contextlib import ExitStack

    send, send_photo = _init_handler()
    completed = threading.Event()

    err = ValueError("Persona 'persona_test01' not found")
    patches = _handler_infra_patches(AsyncMock(side_effect=err))

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        with patch.object(threading.Thread, "start", _fake_start_sync(completed)):
            handle_persona_photo(42, "persona_test01 smile")
            completed.wait(timeout=5)

    messages = [c[0][1] for c in send.call_args_list]
    assert any("not found" in m or "Persona" in m for m in messages), (
        f"Expected error message, got: {messages}"
    )


def test_handle_persona_photo_success():
    from app.handlers.persona_handler import handle_persona_photo
    from contextlib import ExitStack

    send, send_photo = _init_handler()
    completed = threading.Event()

    mock_result = {
        "image_url": "https://replicate.delivery/out/photo.jpg",
        "cost_usd": 0.02,
        "full_prompt": "sks_testbot in a park",
    }
    patches = _handler_infra_patches(AsyncMock(return_value=mock_result))

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        with patch.object(threading.Thread, "start", _fake_start_sync(completed)):
            handle_persona_photo(42, "persona_test01 in a park")
            completed.wait(timeout=5)

    send_photo.assert_called_once()
    call_args = send_photo.call_args[0]
    assert call_args[1] == "https://replicate.delivery/out/photo.jpg"
    assert "sks_testbot" in call_args[2]
