# -*- coding: utf-8 -*-
"""Ш1 яруса 2 — детектор «healthcheck-пинг старше своего grace-окна».

Порог не выдуман: `scripts/register_healthchecks_ping.ps1` объявляет контракт
Period 5 мин / Grace 10 мин, то есть healthchecks.io поднимает тревогу после
~15 минут тишины. Наш детектор смотрит на ТУ ЖЕ границу с другой стороны — по
локальной метке, — чтобы «дед-ман умер молча» не осталось незамеченным.

Два класса молчания:
  * пинг свежий — тишина;
  * метки НЕТ вовсе — это другой класс («пинг не настроен»), а не «старая
    метка»; выдавать одно за другое значит врать в карточке.
"""
from __future__ import annotations

from app.services import autonomy_detectors as det

MINUTE = 60.0


def _snapshot(age):
    return {"stamps": {"healthchecks_age_sec": age}}


def test_stale_ping_is_a_proposal():
    """20 минут тишины при окне 15 — дед-ман уже должен был сработать."""
    found = det.detect_stale_healthcheck(_snapshot(20 * MINUTE))

    assert len(found) == 1
    assert found[0].kind == "healthcheck_stamp_stale"
    assert found[0].subject == "healthchecks"
    assert found[0].evidence["threshold_minutes"] == 15.0


def test_fresh_ping_is_silent():
    assert det.detect_stale_healthcheck(_snapshot(2 * MINUTE)) == []


def test_missing_stamp_is_silent_because_it_is_a_different_class():
    """Метки нет — это «пинг не настроен», отдельная история. Назвать её
    «метка старая» значит выдать неверную причину в карточке."""
    assert det.detect_stale_healthcheck(_snapshot(None)) == []


def test_threshold_is_period_plus_grace_and_comes_from_config():
    assert det.DEFAULT_CONFIG["healthchecks_period_sec"] == 5 * MINUTE
    assert det.DEFAULT_CONFIG["healthchecks_grace_sec"] == 10 * MINUTE
    assert det.detect_stale_healthcheck(
        _snapshot(20 * MINUTE),
        config={"healthchecks_grace_sec": 30 * MINUTE}) == []


def test_stamp_from_the_future_is_not_called_stale():
    """Отрицательный возраст — это баг эпохи +3ч, пойманный 31.07
    (`Get-Date -UFormat %s` считал от ЛОКАЛЬНОГО времени). Метка в будущем —
    не «старая»; своего детектора у этого класса пока нет, и врать в карточке
    хуже, чем молчать."""
    assert det.detect_stale_healthcheck(_snapshot(-3 * 3600.0)) == []


def test_evidence_carries_no_volatile_fact():
    evidence = det.detect_stale_healthcheck(_snapshot(20 * MINUTE))[0].evidence

    assert "age_sec" not in evidence
    assert set(evidence) == {"stamp", "threshold_minutes", "stamp_present"}
