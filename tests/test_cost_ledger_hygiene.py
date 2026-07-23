# -*- coding: utf-8 -*-
"""Гигиена леджера расходов: тесты не могут писать в прод, мусор не удаляется молча."""
from __future__ import annotations

import json

import pytest

from app.services.audit import cost_tracker
from app.services.audit.cost_ledger_clean import (
    apply_quarantine, build_report, classify,
)


# ── физический гард: тест не может дотянуться до боевого леджера ───────────

def test_state_file_refuses_prod_ledger_under_pytest(monkeypatch):
    """Соглашение защищает тех, кто его соблюдает; гард — всех остальных.

    Так в боевой леджер и попали uid 42 ($58.80) и 7 ($33.60): тест-модули
    выставляли JARVIS_COST_FILE каждый сам, и кто забывал — писал в прод."""
    monkeypatch.delenv("JARVIS_COST_FILE", raising=False)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "test_x (call)")
    with pytest.raises(RuntimeError) as exc:
        cost_tracker._state_file()
    assert "БОЕВОМУ" in str(exc.value)
    assert "JARVIS_COST_FILE" in str(exc.value)


def test_state_file_honours_explicit_override(monkeypatch, tmp_path):
    target = tmp_path / "ledger.json"
    monkeypatch.setenv("JARVIS_COST_FILE", str(target))
    assert cost_tracker._state_file() == target


def test_autouse_fixture_already_points_away_from_prod():
    """Сквозная проверка самой фикстуры: в обычном тесте путь уже не боевой."""
    assert "cost_tracking.json" in str(cost_tracker._state_file())
    assert cost_tracker._state_file() != cost_tracker._DEFAULT_STATE_FILE


def test_record_cost_writes_only_to_the_isolated_file(tmp_path, monkeypatch):
    target = tmp_path / "iso.json"
    monkeypatch.setenv("JARVIS_COST_FILE", str(target))
    cost_tracker.record_cost("237616472", "daniil", 1.23)
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["users"]["237616472"]["all_time"] == pytest.approx(1.23)


# ── карантин: показать вычитаемое, ничего не удаляя ────────────────────────

LEDGER = {
    "users": {
        "237616472": {"username": "DaniiLapin13", "all_time": 113.66, "daily": {}, "monthly": {}},
        "545893540": {"username": "Artem_koval3", "all_time": 77.27, "daily": {}, "monthly": {}},
        "42": {"username": None, "all_time": 58.80, "daily": {}, "monthly": {}},
        "7": {"username": "u", "all_time": 33.60, "daily": {}, "monthly": {}},
        "111": {"username": "tester", "all_time": 0.57, "daily": {}, "monthly": {}},
    }
}


def test_classify_separates_fixtures_from_real_users():
    suspect, clean = classify(LEDGER["users"])
    assert set(suspect) == {"42", "7", "111"}
    assert set(clean) == {"237616472", "545893540"}


def test_report_shows_what_is_subtracted():
    """Владелец должен увидеть вычитаемое, а не получить новую цифру без объяснения."""
    r = build_report(LEDGER)
    assert "58.80" in r and "33.60" in r
    assert "вычитается" in r
    assert "остаётся реальным" in r
    # честная оговорка про природу чисел обязана быть в отчёте
    assert "ОЦЕНОК" in r and "Replicate" in r


def test_quarantine_moves_but_never_deletes():
    state = json.loads(json.dumps(LEDGER))
    out = apply_quarantine(state, now="2026-07-24T03:00:00")
    assert set(out["users"]) == {"237616472", "545893540"}
    assert set(out["quarantine"]) == {"42", "7", "111"}
    # данные целы, а не обнулены
    assert out["quarantine"]["42"]["all_time"] == 58.80
    assert out["quarantine"]["42"]["_quarantined_reason"]


def test_quarantine_is_idempotent():
    state = json.loads(json.dumps(LEDGER))
    once = apply_quarantine(state, now="2026-07-24T03:00:00")
    twice = apply_quarantine(json.loads(json.dumps(once)), now="2026-07-24T04:00:00")
    assert set(twice["users"]) == set(once["users"])
    assert set(twice["quarantine"]) == set(once["quarantine"])


def test_real_users_are_never_quarantined():
    """Ложное срабатывание тут стоит дороже пропуска: вычесть реального
    пользователя значит соврать владельцу в меньшую сторону."""
    state = json.loads(json.dumps(LEDGER))
    out = apply_quarantine(state, now="2026-07-24T03:00:00")
    assert out["users"]["237616472"]["all_time"] == 113.66
    assert out["users"]["545893540"]["all_time"] == 77.27
