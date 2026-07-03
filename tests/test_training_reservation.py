# -*- coding: utf-8 -*-
"""Money hole (c): reserve estimated training cost at kickoff, reconcile on success.

LoRA trainings run in a daemon thread and record cost only AFTER Replicate
returns. A bot restart mid-training kills the thread first, so the charge is
invisible to both ledgers. Fix: record the ESTIMATE at kickoff (reservation);
on success record the DELTA (actual − est) so the total equals the actual. On
restart the reservation stays (honest — the charge likely happened).

$0, mocks only, zero real API.
"""
import pytest

import app.handlers.persona_handler as ph


def _thread_noop():
    class _T:
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass          # simulates the daemon thread NOT completing (restart)
    return _T


def _thread_sync():
    class _T:
        def __init__(self, target=None, **k):
            self._t = target

        def start(self):
            self._t()     # run the worker synchronously in-test
    return _T


def _allow(uid, *, estimated_usd):
    return (True, "")


# ── Task 7: /train_lora reserve + delta ───────────────────────────────────
def test_train_lora_reserves_est_at_kickoff(monkeypatch):
    recs = []
    monkeypatch.setattr(ph, "check_limit", _allow)
    monkeypatch.setattr(ph, "_record_user_cost", lambda cid, amt: recs.append(amt))
    monkeypatch.setattr(ph, "_safe_send", lambda *a, **k: None)
    monkeypatch.setattr(ph.threading, "Thread", _thread_noop())
    ph._do_train_lora(237616472, "persona1")
    # Reservation recorded synchronously; thread never completed (restart sim).
    assert recs == [pytest.approx(ph._train_lora_est())]


def test_train_lora_delta_on_success_reconciles_to_actual(monkeypatch):
    recs = []
    captured = {}
    monkeypatch.setattr(ph, "check_limit", _allow)
    monkeypatch.setattr(ph, "_record_user_cost", lambda cid, amt: recs.append(amt))
    monkeypatch.setattr(ph, "_safe_send", lambda *a, **k: None)
    monkeypatch.setattr(ph, "setup_block_m_logging", lambda *a, **k: None)
    monkeypatch.setattr(ph, "PersonaStorage", lambda *a, **k: object())
    monkeypatch.setattr(ph, "ReplicateVideoClient", lambda *a, **k: object())
    monkeypatch.setattr(ph, "CostTracker", lambda *a, **k: object())
    monkeypatch.setattr(ph, "VideoQueue", lambda *a, **k: object())

    class _Trainer:
        def __init__(self, *a, **k):
            pass

        async def start_training(self, persona_id, *, user_chat_id=None,
                                 notify_fn=None, on_success_cost=None):
            captured["cb"] = on_success_cost

    monkeypatch.setattr(ph, "LoRATrainer", _Trainer)
    monkeypatch.setattr(ph.threading, "Thread", _thread_sync())

    ph._do_train_lora(237616472, "persona1")
    est = ph._train_lora_est()
    assert recs[0] == pytest.approx(est)          # reservation
    # engine reports actual cost -> delta recorded, total == actual
    captured["cb"](1.80)
    assert recs[1] == pytest.approx(1.80 - est)
    assert sum(recs) == pytest.approx(1.80)


# ── Task 7: /me_done reserve-only (no actual available) ───────────────────
def test_me_done_reserves_est_at_kickoff(monkeypatch):
    recs = []
    monkeypatch.setattr(ph, "check_limit", _allow)
    monkeypatch.setattr(ph, "_record_user_cost", lambda cid, amt: recs.append(amt))
    monkeypatch.setattr(ph, "_safe_send", lambda *a, **k: None)
    monkeypatch.setattr(ph.threading, "Thread", _thread_noop())
    ph._do_train_me_lora(237616472)
    # me_done had NO ledger write before; now reserves its estimate at kickoff.
    assert recs == [pytest.approx(ph._train_me_lora_est())]


# ── Task 7: create_persona seed reserve + delta ───────────────────────────
class _FakeDialog:
    name = "p"
    description = "d"
    style = "s"
    persona_id = None

    def confirm(self):
        pass

    def set_persona_id(self, pid):
        self.persona_id = pid

    def cancel(self):
        pass


def test_create_persona_seed_reserves_est_at_kickoff(monkeypatch):
    recs = []
    monkeypatch.setattr(ph, "check_limit", _allow)
    monkeypatch.setattr(ph, "_record_user_cost", lambda cid, amt: recs.append(amt))
    monkeypatch.setattr(ph, "_safe_send", lambda *a, **k: None)
    monkeypatch.setattr(ph.threading, "Thread", _thread_noop())
    ph._start_generation(237616472, _FakeDialog())
    assert recs == [pytest.approx(ph._create_persona_est())]
