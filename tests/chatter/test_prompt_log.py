"""Логирование структуры промпта (спека §8): PII-free структурная строка +
полный дамп ТОЛЬКО по флагу CHATTER_PROMPT_DUMP."""
from __future__ import annotations

import logging

from chatter.core.obligations_slot import Obligation, merge_obligations
from chatter.core.prompt_log import log_prompt_shape, obligations_digest


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
