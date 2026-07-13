"""Зуб живого класса бага: «record_cost вызван» ≠ «/costs показывает».

Интеграционный write→read через РЕАЛЬНЫЙ ридер /costs (handle_costs): пишем трату
как guard_spend/record_cost (audit-леджер cost_tracking.json), затем читаем её тем же
путём, что использует /costs, и убеждаемся, что она видна. Мок record_cost НЕ годится —
именно разрыв между записью и чтением этот зуб и закрывает.

/costs теперь показывает системный N-дневный свод (по дням + сравнение с
предыдущим периодом), а не построчно по пользователям — поэтому суммы
нескольких плательщиков складываются в один тотал за день, а не
перечисляются отдельными подстроками.
"""
import app.handlers.persona_handler as ph
from app.services.audit import cost_tracker as ct


def test_costs_shows_spend_recorded_via_audit_ledger(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_COST_FILE", str(tmp_path / "cost.json"))
    monkeypatch.setenv("JARVIS_EXPENSES_FILE", str(tmp_path / "expenses.jsonl"))
    # запись ровно как guard_spend → record_cost (audit-леджер), картошка $0.04
    ct.record_cost(237616472, "DaniiLapin13", 0.04)

    sent = []
    monkeypatch.setattr(ph, "_safe_send", lambda cid, t, **k: sent.append(t))
    ph.handle_costs(237616472)

    out = "\n".join(sent)
    assert "0.04" in out            # трата ВИДНА в выводе /costs (итого/по дням)


def test_costs_reflects_multiple_users_from_audit(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_COST_FILE", str(tmp_path / "cost.json"))
    monkeypatch.setenv("JARVIS_EXPENSES_FILE", str(tmp_path / "expenses.jsonl"))
    ct.record_cost(237616472, "DaniiLapin13", 0.04)   # admin
    ct.record_cost(545893540, "Artem_koval3", 0.10)   # friend

    sent = []
    monkeypatch.setattr(ph, "_safe_send", lambda cid, t, **k: sent.append(t))
    ph.handle_costs(237616472)

    out = "\n".join(sent)
    assert "0.14" in out            # audit-свод складывает обеих плательщиков в тотал
