# -*- coding: utf-8 -*-
"""/persona_status <persona_id|all> — read-only status card. $0, mocks only.

Covers pure card formatting (LoRA draft/prod label, seed count, generation
count, scale/guidance, weights-alive HEAD check) and the handler's I/O glue
(persona lookup, LoRA training-status lookup, HEAD probe) via monkeypatched
storage/trainer/queue/tracker — never touches state/personas or the network.
"""
from __future__ import annotations

from datetime import datetime

import app.handlers.persona_handler as ph
from app.services.block_m_common.persona_storage import Persona

ADMIN = 237616472


def _persona(**overrides):
    base = dict(
        persona_id="persona_abc123",
        name="Sofia",
        description="25, blue eyes",
        style="fashion",
        lora_weights_url=None,
        trigger_word="sks_persona_abc123",
        created_at=datetime(2026, 7, 1, 12, 0, 0),
        seed_photos=["a.jpg", "b.jpg"],
        total_generations=0,
        total_cost_usd=0.0,
    )
    base.update(overrides)
    return Persona(**base)


# ── pure formatting ─────────────────────────────────────────────────────────


def test_card_labels_prod_when_weights_trained():
    p = _persona(
        lora_weights_url="https://weights.example/x.safetensors",
        total_generations=5,
        seed_photos=["a.jpg"] * 12,
    )
    status = {"status": "done", "progress_pct": 100, "weights_url": p.lora_weights_url, "error": None}
    card = ph._format_persona_status_card(p, status, weights_alive=True, scale=1.0, guidance=4.0)
    assert "LoRA: prod" in card
    assert "Seed-фото: 12" in card
    assert "Генераций сделано: 5" in card
    assert "scale=1.0" in card and "guidance=4.0" in card
    assert "Weights URL: жив" in card


def test_card_labels_draft_when_training_in_progress():
    p = _persona()
    status = {"status": "training", "progress_pct": 50, "weights_url": None, "error": None}
    card = ph._format_persona_status_card(p, status, weights_alive=None, scale=1.0, guidance=4.0)
    assert "LoRA: draft" in card
    assert "Weights URL: — (LoRA не натренирована)" in card


def test_card_labels_no_lora_when_never_trained():
    p = _persona()
    card = ph._format_persona_status_card(p, None, weights_alive=None, scale=1.0, guidance=4.0)
    assert "LoRA: нет LoRA" in card


def test_card_flags_dead_weights_url():
    p = _persona(lora_weights_url="https://dead.example/x.safetensors")
    status = {"status": "done", "progress_pct": 100, "weights_url": p.lora_weights_url, "error": None}
    card = ph._format_persona_status_card(p, status, weights_alive=False, scale=1.0, guidance=4.0)
    assert "Weights URL: МЁРТВ" in card


def test_summary_line_shows_lora_and_generations():
    p = _persona(name="Sofia", total_generations=3)
    line = ph._format_persona_status_summary_line(p)
    assert "Sofia" in line
    assert "3" in line


# ── dispatch / I/O glue ──────────────────────────────────────────────────────


class _FakeStorage:
    def __init__(self, personas):
        self._by_id = {p.persona_id: p for p in personas}

    async def get_persona(self, persona_id):
        return self._by_id.get(persona_id)

    async def list_personas(self):
        return list(self._by_id.values())


class _FakeTrainer:
    def __init__(self, status):
        self._status = status

    async def check_status(self, persona_id):
        return self._status


def _patch_no_op_deps(monkeypatch):
    """Prevent real CostTracker()/VideoQueue() construction (mkdir side effect)."""
    monkeypatch.setattr(ph, "CostTracker", lambda *a, **k: object())
    monkeypatch.setattr(ph, "VideoQueue", lambda *a, **k: object())


def test_handle_persona_status_no_args_shows_usage(monkeypatch):
    sent = []
    monkeypatch.setattr(ph, "_safe_send", lambda cid, t: sent.append(t))
    ph.handle_persona_status(ADMIN, "")
    assert "persona_status" in sent[0]


def test_handle_persona_status_unknown_persona_is_honest(monkeypatch):
    sent = []
    monkeypatch.setattr(ph, "_safe_send", lambda cid, t: sent.append(t))
    monkeypatch.setattr(ph, "PersonaStorage", lambda: _FakeStorage([]))
    ph.handle_persona_status(ADMIN, "persona_nope")
    assert "не найдена" in sent[0]


def test_handle_persona_status_trained_persona_checks_weights_alive(monkeypatch):
    p = _persona(lora_weights_url="https://weights.example/x.safetensors", total_generations=7)
    sent = []
    _patch_no_op_deps(monkeypatch)
    monkeypatch.setattr(ph, "_safe_send", lambda cid, t: sent.append(t))
    monkeypatch.setattr(ph, "PersonaStorage", lambda: _FakeStorage([p]))
    monkeypatch.setattr(
        ph, "LoRATrainer",
        lambda *a, **k: _FakeTrainer(
            {"status": "done", "progress_pct": 100, "weights_url": p.lora_weights_url, "error": None}
        ),
    )
    alive_calls = []
    monkeypatch.setattr(
        "app.services.ig_media_prep.is_media_url_alive",
        lambda url: alive_calls.append(url) or True,
    )

    ph.handle_persona_status(ADMIN, p.persona_id)

    assert alive_calls == [p.lora_weights_url]
    assert "жив" in sent[0]
    assert "Генераций сделано: 7" in sent[0]


def test_handle_persona_status_untrained_persona_skips_head_check(monkeypatch):
    p = _persona()
    sent = []
    _patch_no_op_deps(monkeypatch)
    monkeypatch.setattr(ph, "_safe_send", lambda cid, t: sent.append(t))
    monkeypatch.setattr(ph, "PersonaStorage", lambda: _FakeStorage([p]))
    monkeypatch.setattr(ph, "LoRATrainer", lambda *a, **k: _FakeTrainer(None))
    called = []
    monkeypatch.setattr(
        "app.services.ig_media_prep.is_media_url_alive",
        lambda url: called.append(url),
    )

    ph.handle_persona_status(ADMIN, p.persona_id)

    assert called == []
    assert "LoRA: нет LoRA" in sent[0]


def test_handle_persona_status_all_lists_every_persona(monkeypatch):
    p1 = _persona(persona_id="persona_1", name="Sofia")
    p2 = _persona(persona_id="persona_2", name="Anna", lora_weights_url="https://w/x")
    sent = []
    monkeypatch.setattr(ph, "_safe_send", lambda cid, t: sent.append(t))
    monkeypatch.setattr(ph, "PersonaStorage", lambda: _FakeStorage([p1, p2]))

    ph.handle_persona_status(ADMIN, "all")

    assert "Sofia" in sent[0]
    assert "Anna" in sent[0]


def test_handle_persona_status_all_empty_is_honest(monkeypatch):
    sent = []
    monkeypatch.setattr(ph, "_safe_send", lambda cid, t: sent.append(t))
    monkeypatch.setattr(ph, "PersonaStorage", lambda: _FakeStorage([]))

    ph.handle_persona_status(ADMIN, "all")

    assert "Персон пока нет" in sent[0]


def test_handle_persona_status_error_is_honest_not_silent(monkeypatch):
    sent = []
    monkeypatch.setattr(ph, "_safe_send", lambda cid, t: sent.append(t))

    def _boom():
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(ph, "PersonaStorage", _boom)
    ph.handle_persona_status(ADMIN, "persona_x")
    assert "disk on fire" in sent[0] or "Ошибка" in sent[0]
