from __future__ import annotations

import logging

from chatter.core.escalation import advance_funnel
from chatter.storage.db import Store


def _store():
    s = Store(":memory:")
    s.get_or_create_contact("c1")
    return s


def test_engaged_advances_new_to_qualifying():
    s = _store()
    new_state = advance_funnel(s, "c1", stage_signal="engaged", escalated=False)
    assert new_state == "qualifying"
    assert s.get_or_create_contact("c1")["state"] == "qualifying"


def test_escalated_forces_escalated_state_regardless_of_signal():
    # Эскалация всегда уводит в 'escalated', даже если сигнал классификатора
    # был про что-то другое (или его нет).
    s = _store()
    s.set_state("c1", "qualifying")
    new_state = advance_funnel(s, "c1", stage_signal="interested", escalated=True)
    assert new_state == "escalated"
    assert s.get_or_create_contact("c1")["state"] == "escalated"


def test_none_signal_no_escalation_leaves_state_unchanged():
    s = _store()
    s.set_state("c1", "qualifying")
    new_state = advance_funnel(s, "c1", stage_signal=None, escalated=False)
    assert new_state == "qualifying"
    assert s.get_or_create_contact("c1")["state"] == "qualifying"


def test_unknown_signal_is_noop():
    s = _store()
    s.set_state("c1", "qualifying")
    new_state = advance_funnel(s, "c1", stage_signal="banana", escalated=False)
    assert new_state == "qualifying"


def test_interested_advances_new_straight_to_hot():
    """Сквозь весь конвейер, не только карту: лид, заявивший интерес первым
    сообщением, обязан доехать до `hot` и записаться в БД."""
    s = _store()
    assert advance_funnel(s, "c1", stage_signal="interested", escalated=False) == "hot"
    assert s.get_or_create_contact("c1")["state"] == "hot"


def test_needs_human_from_new_escalates_without_escalate_flag():
    """Важный случай: `escalate` и `stage_signal` — НЕЗАВИСИМЫЕ поля JSON
    классификатора (decide_escalation). Оверрайд escalated=True спасал лида в
    `new` только когда классификатор выставлял ОБА. Здесь он выставил один."""
    s = _store()
    assert advance_funnel(s, "c1", stage_signal="needs_human",
                          escalated=False) == "escalated"
    assert s.get_or_create_contact("c1")["state"] == "escalated"


def test_unknown_info_from_new_escalates():
    s = _store()
    assert advance_funnel(s, "c1", stage_signal="unknown_info",
                          escalated=False) == "escalated"


def test_new_to_hot_is_recorded_as_transition():
    """Переход обязан попасть в funnel_transitions — иначе «Динамика» покажет
    лида телепортировавшимся."""
    s = _store()
    advance_funnel(s, "c1", stage_signal="interested", escalated=False, now=100.0)
    rows = list(s.list_funnel_transitions()) if hasattr(s, "list_funnel_transitions") \
        else list(s._conn.execute("SELECT from_state,to_state,signal FROM funnel_transitions"))
    assert any(tuple(r)[:3] == ("new", "hot", "interested") for r in rows), rows


def test_terminal_state_stays_terminal():
    s = _store()
    s.set_state("c1", "closed")
    new_state = advance_funnel(s, "c1", stage_signal="engaged", escalated=True)
    assert new_state == "closed"
    assert s.get_or_create_contact("c1")["state"] == "closed"


# --- проводка лога воронки ---------------------------------------------------
# Урок слота обязательств: логировать ОБА конца. «Функция умеет писать строку»
# и «строка появляется на живом ходу» — разные утверждения, и путать их дорого.

def test_advance_funnel_actually_emits_the_log_line(caplog):
    s = _store()
    with caplog.at_level(logging.INFO, logger="chatter.core.prompt_log"):
        advance_funnel(s, "c1", stage_signal="interested", escalated=False)
    assert "funnel" in caplog.text and "new->hot" in caplog.text
    assert "changed=yes" in caplog.text


def test_advance_funnel_logs_even_when_nothing_changed(caplog):
    """Холостой ход в БД не пишется по дизайну — но в логе быть обязан."""
    s = _store()
    s.set_state("c1", "escalated")
    with caplog.at_level(logging.INFO, logger="chatter.core.prompt_log"):
        advance_funnel(s, "c1", stage_signal="interested", escalated=False)
    assert "changed=no" in caplog.text and "signal=interested" in caplog.text


def test_advance_funnel_log_failure_does_not_break_the_turn(monkeypatch, caplog):
    """DEV-18: наблюдаемость не имеет права уронить доставку ответа лиду."""
    import chatter.core.escalation as esc

    def boom(**kw):
        raise RuntimeError("логгер упал")
    monkeypatch.setattr(esc, "log_funnel_signal", boom)
    s = _store()
    assert advance_funnel(s, "c1", stage_signal="interested", escalated=False) == "hot"
    assert s.get_or_create_contact("c1")["state"] == "hot"
