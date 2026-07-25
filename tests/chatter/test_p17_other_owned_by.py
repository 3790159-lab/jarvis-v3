# -*- coding: utf-8 -*-
"""P17: у свободной корзины `other` владелец долга обязан быть НАЗВАН.

Живой дрил 2026-07-25 нашёл у volska два открытых обязательства с owed_by=bot,
по смыслу — ход КЛИЕНТА («клієнт ще не оплатив»). Корень не в модели: поля
owed_by не было в схеме вовсе, а merge_obligations подставлял `bot` по умолчанию —
значит ЛЮБОЙ other молча становился долгом бота и дожимался в промпте brain.

Спека: docs/superpowers/specs/2026-07-25-chatter-p17-other-owned-by.md
"""
from __future__ import annotations

import logging

from chatter.core.classifier import classifier_stable_prefix, classifier_system_prompt
from chatter.core.obligations_slot import (
    filter_model_updates, merge_obligations, render_slot_block,
)

NOW = 1_000_000.0


def _merge(updates, existing=()):
    return merge_obligations(list(existing), filter_model_updates(updates, existing),
                             now=NOW, current_msg_id=1)


# ── антипод: клиентский факт НЕ становится долгом бота ───────────────────────


def test_client_owned_other_is_stored_but_never_rendered_to_brain():
    """Главный тест P17: факт про ход клиента живёт в слоте (топливо client-owed
    слоя §13), но в «ВІДКРИТІ ЗОБОВ'ЯЗАННЯ» бота не попадает."""
    obs = _merge([{"kind": "other", "owed_by": "client", "status": "open",
                   "detail": "клієнт ще не оплатив"}])
    assert len(obs) == 1 and obs[0].owed_by == "client" and obs[0].status == "open"
    block = render_slot_block(obs, now=NOW)
    assert "клієнт ще не оплатив" not in block
    assert block == "", "единственное обязательство — клиентское, блока быть не должно"


def test_bot_owned_other_is_still_rendered():
    """Свободные обещания САМОГО бота теряться не должны."""
    obs = _merge([{"kind": "other", "owed_by": "bot", "status": "open",
                   "detail": "надішлю договір у понеділок"}])
    assert "надішлю договір" in render_slot_block(obs, now=NOW)


def test_other_without_owner_is_dropped_loudly(caplog):
    """Дефолт `bot` — это и есть корень P17. Для other пропуск владельца больше
    не молчит: строка не создаётся, в логе warning."""
    with caplog.at_level(logging.WARNING):
        obs = _merge([{"kind": "other", "status": "open", "detail": "щось невизначене"}])
    assert obs == []
    assert any("owed_by" in r.message or "owed_by" in str(r.args) for r in caplog.records)


def test_canonical_kinds_keep_the_code_invariant():
    """brief/examples/recalc владельца НЕ выбирают: что бы ни пришло — bot."""
    obs = _merge([{"kind": "brief", "status": "delivered", "detail": "питання задані"},
                  {"kind": "recalc", "owed_by": "client", "status": "open",
                   "detail": "порахувати візитівки"}])
    assert {o.kind: o.owed_by for o in obs} == {"brief": "bot", "recalc": "bot"}


def test_owner_write_still_created_as_bot_without_the_field():
    obs = _merge([{"kind": "owner_write", "status": "open", "detail": "керівниця напише"}])
    assert len(obs) == 1 and obs[0].owed_by == "bot"


# ── контракт промпта ─────────────────────────────────────────────────────────


def _prompts():
    """Обе сборки: кэшируемый префикс (живой путь) и старая одной строкой
    (ветка отката). Расхождение между ними — тихая мина."""
    return [classifier_stable_prefix("PB", "uk", track_obligations=True),
            classifier_system_prompt("PB", "uk", track_obligations=True,
                                     obligations_block="")]


def test_schema_asks_owner_only_for_other():
    for p in _prompts():
        assert "owed_by" in p, "поля нет в схеме — модель не сможет назвать владельца"
        assert "other" in p


def test_prompt_carries_the_anti_example():
    """Описания формата мало — это уже проверено на классификаторе дважды.
    Показываем сам провал: «клієнт ще не оплатив» — не долг бота."""
    for p in _prompts():
        assert "клієнт ще не оплатив" in p
