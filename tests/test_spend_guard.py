"""Зубы money-гейта: guard_spend инкапсулирует check_limit(до траты) + record_cost(после успеха)."""
import app.services.auth.spend_guard as sg


def _patch(monkeypatch, allowed, reason=""):
    calls = {"check": [], "record": [], "spend": 0}
    monkeypatch.setattr(
        sg, "check_limit",
        lambda uid, estimated_usd: (calls["check"].append((uid, estimated_usd)) or (allowed, reason)),
    )
    monkeypatch.setattr(
        sg.cost_tracker, "record_cost",
        lambda uid, un, amt: calls["record"].append((uid, un, amt)),
    )
    return calls


def test_over_limit_blocks_before_spend(monkeypatch):
    calls = _patch(monkeypatch, allowed=False, reason="Дневной лимит $1.00 исчерпан")

    def do():  # ДОЛЖЕН НЕ вызываться
        calls["spend"] += 1
        return "img"

    result, err = sg.guard_spend(42, None, 0.04, do)
    assert result is None
    assert "лимит" in err.lower()
    assert calls["spend"] == 0        # ЗУБ 1: трата без гейта невозможна
    assert calls["record"] == []      # ничего не списано


def test_success_records_ledger(monkeypatch):
    calls = _patch(monkeypatch, allowed=True)
    result, err = sg.guard_spend(42, None, 0.04, lambda: "http://img")
    assert result == "http://img"
    assert err is None
    assert calls["record"] == [(42, None, 0.04)]   # ЗУБ 2a: успех → леджер


def test_failure_does_not_record(monkeypatch):
    calls = _patch(monkeypatch, allowed=True)
    result, err = sg.guard_spend(42, None, 0.04, lambda: None)  # сервис вернул None
    assert result is None
    assert err is None
    assert calls["record"] == []                    # ЗУБ 2b: провал → НЕ платим


def test_admin_unlimited_still_records(monkeypatch):
    calls = _patch(monkeypatch, allowed=True)       # admin всегда allowed
    sg.guard_spend(999, "admin", 0.04, lambda: "ok")
    assert calls["record"] == [(999, "admin", 0.04)]  # чинит неучёт admin в /costs


def test_record_failure_never_breaks_delivery(monkeypatch):
    _patch(monkeypatch, allowed=True)

    def boom(*_a, **_k):
        raise RuntimeError("ledger down")

    monkeypatch.setattr(sg.cost_tracker, "record_cost", boom)
    # доставка не должна падать из-за сбоя учёта
    result, err = sg.guard_spend(42, None, 0.04, lambda: "http://img")
    assert result == "http://img"
    assert err is None
