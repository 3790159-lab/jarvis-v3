"""Зубы пре-гейта me_swap_photo/video (T7 Арки 1).

me_swap уже пишет ФАКТИЧЕСКИЙ cost_usd в леджер на успехе (частичная дыра —
нет пре-гейта). T7 добавляет check_limit СТРОГО до запуска генерации: over-limit →
поток генерации не стартует, трат нет. Существующий record остаётся (не задваиваем).
"""
import app.handlers.persona_handler as ph


class _FakeThread:
    def __init__(self, *a, **k):
        pass

    def start(self):
        _FakeThread.started += 1


def _patch_thread(monkeypatch):
    _FakeThread.started = 0
    monkeypatch.setattr(ph.threading, "Thread", _FakeThread)


def test_me_swap_photo_over_limit_no_generation(monkeypatch):
    _patch_thread(monkeypatch)
    monkeypatch.setattr(ph, "check_limit", lambda uid, estimated_usd: (False, "Дневной лимит $1.00 исчерпан"), raising=False)
    sent = []
    monkeypatch.setattr(ph, "_safe_send", lambda cid, t, **k: sent.append(t))
    ph.handle_me_swap_photo(42, "на пляже")
    assert _FakeThread.started == 0                       # генерация не стартовала
    assert any("🚫" in s or "лимит" in s.lower() for s in sent)


def test_me_swap_photo_gate_pass_starts_generation(monkeypatch):
    _patch_thread(monkeypatch)
    monkeypatch.setattr(ph, "check_limit", lambda uid, estimated_usd: (True, ""), raising=False)
    monkeypatch.setattr(ph, "_safe_send", lambda *a, **k: None)
    ph.handle_me_swap_photo(42, "на пляже")
    assert _FakeThread.started == 1


def test_me_swap_video_over_limit_no_generation(monkeypatch):
    _patch_thread(monkeypatch)
    monkeypatch.setattr(ph, "check_limit", lambda uid, estimated_usd: (False, "лимит"), raising=False)
    sent = []
    monkeypatch.setattr(ph, "_safe_send", lambda cid, t, **k: sent.append(t))
    ph.handle_me_swap_video(42, "http://video")
    assert _FakeThread.started == 0
    assert any("🚫" in s or "лимит" in s.lower() for s in sent)


def test_me_swap_video_gate_pass_starts_generation(monkeypatch):
    _patch_thread(monkeypatch)
    monkeypatch.setattr(ph, "check_limit", lambda uid, estimated_usd: (True, ""), raising=False)
    monkeypatch.setattr(ph, "_safe_send", lambda *a, **k: None)
    ph.handle_me_swap_video(42, "http://video")
    assert _FakeThread.started == 1


# ── T6 пре-гейт train_lora / create_persona (record факта — в block_m юнит-тестах) ──

def test_train_lora_over_limit_never_starts(monkeypatch):
    _patch_thread(monkeypatch)
    monkeypatch.setattr(ph, "check_limit", lambda uid, estimated_usd: (False, "лимит"), raising=False)
    sent = []
    monkeypatch.setattr(ph, "_safe_send", lambda cid, t, **k: sent.append(t))
    ph._do_train_lora(42, "persona_x")
    assert _FakeThread.started == 0
    assert any("🚫" in s or "лимит" in s.lower() for s in sent)


def test_train_lora_gate_pass_starts(monkeypatch):
    _patch_thread(monkeypatch)
    monkeypatch.setattr(ph, "check_limit", lambda uid, estimated_usd: (True, ""), raising=False)
    monkeypatch.setattr(ph, "_safe_send", lambda *a, **k: None)
    ph._do_train_lora(42, "persona_x")
    assert _FakeThread.started == 1


def test_train_lora_gate_uses_train_cost_estimate(monkeypatch):
    seen = {}
    _patch_thread(monkeypatch)
    monkeypatch.setattr(ph, "check_limit",
                        lambda uid, estimated_usd: seen.update(est=estimated_usd) or (True, ""), raising=False)
    monkeypatch.setattr(ph, "_safe_send", lambda *a, **k: None)
    ph._do_train_lora(42, "persona_x")
    assert seen["est"] >= 1.0                            # ~$2 оценка тренировки


def test_create_persona_over_limit_never_generates(monkeypatch):
    from unittest.mock import MagicMock
    _patch_thread(monkeypatch)
    monkeypatch.setattr(ph, "check_limit", lambda uid, estimated_usd: (False, "лимит"), raising=False)
    sent = []
    monkeypatch.setattr(ph, "_safe_send", lambda cid, t, **k: sent.append(t))
    dialog = MagicMock()
    ph._start_generation(42, dialog)
    assert _FakeThread.started == 0
    dialog.confirm.assert_not_called()                   # генерация не подтверждена
    assert any("🚫" in s or "лимит" in s.lower() for s in sent)


def test_create_persona_gate_pass_starts(monkeypatch):
    from unittest.mock import MagicMock
    _patch_thread(monkeypatch)
    monkeypatch.setattr(ph, "check_limit", lambda uid, estimated_usd: (True, ""), raising=False)
    monkeypatch.setattr(ph, "_safe_send", lambda *a, **k: None)
    dialog = MagicMock()
    ph._start_generation(42, dialog)
    assert _FakeThread.started == 1
    dialog.confirm.assert_called_once()
