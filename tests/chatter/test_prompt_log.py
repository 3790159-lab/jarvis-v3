"""Логирование структуры промпта (спека §8): PII-free структурная строка +
полный дамп ТОЛЬКО по флагу CHATTER_PROMPT_DUMP."""
from __future__ import annotations

import logging

from chatter.core.obligations_slot import Obligation, merge_obligations
from chatter.core.prompt_log import (
    log_funnel_signal, log_prompt_shape, obligations_digest,
)


def _obs():
    return merge_obligations(
        [], [{"kind": "brief", "owed_by": "bot", "status": "open",
              "detail": "СЕКРЕТ-деталь Ольга Іванівна"},
             {"kind": "recalc", "owed_by": "bot", "status": "delivered",
              "detail": "внутрішня примітка"}],
        now=1.0, current_msg_id=1)


def test_digest_is_pii_free():
    d = obligations_digest(_obs())
    assert "СЕКРЕТ" not in d and "Ольга" not in d and "примітка" not in d
    assert "n=2" in d
    assert "brief" in d and "recalc" in d          # kinds — это форма, не ПДн
    assert "open" in d and "delivered" in d


def test_digest_empty():
    assert obligations_digest([]) == "n=0"


def test_digest_reports_renderable_separately_from_total():
    # obl=n считает ВСЕ owed_by, а рендер в brain — только owed_by=bot. Без этого
    # разделения client-owed brief давал n=1 и «выглядел зелёным», хотя в промпт
    # не инъектился (баг Д-10 2026-07-24). renderable = open+owed_by=bot.
    # Строим Obligation напрямую: digest не зависит от merge (а merge отбросил бы
    # other анти-фрагментацией — это другой инвариант, не про digest).
    def _o(okey, kind, owed_by):
        return Obligation(okey=okey, kind=kind, owed_by=owed_by, status="open",
                          detail="x", created_msg_id=1, closed_msg_id=None,
                          created_ts=1.0, closed_ts=None)
    obs = [_o("brief", "brief", "bot"), _o("other:think", "other", "client")]
    d = obligations_digest(obs)
    assert "n=2" in d
    assert "renderable=1" in d          # тільки bot-owed open доедет до brain


def test_shape_line_is_pii_free(caplog):
    with caplog.at_level(logging.INFO, logger="chatter.core.prompt_log"):
        sha = log_prompt_shape(system="ABCDE", suffix="XY",
                               obligations=_obs(), tag="brain",
                               contact_id="237616472")
    line = caplog.text
    assert "prompt-shape" in line and "sys_chars=5" in line and "suffix_chars=2" in line
    assert sha in line
    assert "237616472" not in line                 # сырой id не светится
    assert "СЕКРЕТ" not in line and "Ольга" not in line
    assert "n=2" in line


def test_full_dump_only_when_flag_on(monkeypatch, tmp_path):
    monkeypatch.setenv("CHATTER_PROMPT_DUMP_DIR", str(tmp_path))
    # флаг off → файла нет
    monkeypatch.delenv("CHATTER_PROMPT_DUMP", raising=False)
    log_prompt_shape(system="SYS-текст", suffix="СЕКРЕТ-профіль", tag="brain")
    assert not (tmp_path / "prompt_dump.log").exists()
    # флаг on → полный дамп с ПДн
    monkeypatch.setenv("CHATTER_PROMPT_DUMP", "1")
    log_prompt_shape(system="SYS-текст", suffix="СЕКРЕТ-профіль", tag="brain")
    dump = (tmp_path / "prompt_dump.log").read_text(encoding="utf-8")
    assert "SYS-текст" in dump and "СЕКРЕТ-профіль" in dump   # полный промпт целиком


# --- воронка: снятие слепой зоны stage_signal --------------------------------
# Мотив: stage_signal не логировался НИГДЕ. В БД (`funnel_transitions`) намеренно
# пишется только РЕАЛЬНАЯ смена состояния — холостой ход пропускается, чтобы не
# раздувать метрику. Значит проглоченный сигнал не оставлял следа ни там, ни в
# логе: ровно так дыра `new + interested` и прожила незамеченной.

def test_funnel_line_logs_a_real_transition(caplog):
    with caplog.at_level(logging.INFO, logger="chatter.core.prompt_log"):
        log_funnel_signal(contact_id="12345", signal="interested",
                          from_state="new", to_state="hot", escalated=False)
    line = caplog.text
    assert "funnel" in line
    assert "signal=interested" in line
    assert "new->hot" in line
    assert "changed=yes" in line


def test_funnel_line_logs_swallowed_signal_as_changed_no(caplog):
    """Главный случай: сигнал пришёл, состояние не изменилось. В БД такого хода
    нет по дизайну — лог остаётся единственным местом, где это видно."""
    with caplog.at_level(logging.INFO, logger="chatter.core.prompt_log"):
        log_funnel_signal(contact_id="12345", signal="engaged",
                          from_state="hot", to_state="hot", escalated=False)
    assert "changed=no" in caplog.text
    assert "signal=engaged" in caplog.text


def test_funnel_line_is_pii_free(caplog):
    """Тот же стандарт, что у prompt-shape: сырой contact_id в лог не попадает."""
    with caplog.at_level(logging.INFO, logger="chatter.core.prompt_log"):
        log_funnel_signal(contact_id="237616472", signal="interested",
                          from_state="new", to_state="hot", escalated=False)
    assert "237616472" not in caplog.text


def test_funnel_line_marks_escalation_override(caplog):
    """Оверрайд эскалации обгоняет карту переходов — в логе это должно быть
    отличимо от обычного перехода по сигналу."""
    with caplog.at_level(logging.INFO, logger="chatter.core.prompt_log"):
        log_funnel_signal(contact_id="1", signal=None,
                          from_state="new", to_state="escalated", escalated=True)
    assert "escalated=yes" in caplog.text
    assert "signal=-" in caplog.text
