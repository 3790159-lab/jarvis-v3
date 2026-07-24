"""Расширение классификатора под слот обязательств (спека §4).

Флаг-gated: track_obligations=False → промпт БАЙТ-В-БАЙТ как раньше (часть
приёмки «off = поведение как сейчас»). parse читает obligations[] всегда
(поле отсутствует → пустой кортеж, поведение-нейтрально)."""
from __future__ import annotations

from chatter.core.classifier import parse_classifier_reply, classifier_system_prompt


def test_parse_extracts_obligations():
    raw = ('{"escalate":false,"reason":"r","profile":null,"stage_signal":"engaged",'
           '"obligations":[{"kind":"brief","owed_by":"bot","status":"open","detail":"бриф"}]}')
    res = parse_classifier_reply(raw)
    assert not res.degraded
    assert len(res.obligations) == 1
    assert res.obligations[0]["kind"] == "brief"


def test_parse_absent_obligations_is_empty_tuple():
    raw = '{"escalate":false,"reason":"","profile":null,"stage_signal":"engaged"}'
    assert parse_classifier_reply(raw).obligations == ()


def test_parse_malformed_obligations_ignored_rest_ok():
    res = parse_classifier_reply('{"escalate":true,"reason":"x","obligations":"nope"}')
    assert res.obligations == ()
    assert res.escalate is True


def test_parse_obligations_filters_non_dict_items():
    raw = '{"escalate":false,"obligations":[{"kind":"brief","status":"open"}, 42, "x"]}'
    res = parse_classifier_reply(raw)
    assert len(res.obligations) == 1
    assert res.obligations[0]["kind"] == "brief"


def test_prompt_omits_obligations_when_off_byte_identical_path():
    p = classifier_system_prompt("PB", "uk")
    assert '"obligations"' not in p
    assert "ЗОБОВ'ЯЗАННЯ" not in p


def test_prompt_includes_obligations_when_on():
    p = classifier_system_prompt("PB", "uk", track_obligations=True,
                                 obligations_block="- brief (open): бриф")
    assert '"obligations"' in p            # схема JSON расширена
    assert "ФУНКЦ" in p.upper()            # правило закрытия по функции
    assert "brief" in p                    # текущий блок обязательств вставлен


def test_schema_omits_owed_by_model_choice():
    # owed_by для brief/examples/recalc — инвариант кода (см. filter_model_updates),
    # не поле выбора модели: убираем bot|client из JSON-схемы, чтобы модель его не
    # выставляла ошибочно (баг Д-10 2026-07-24). Гарантия — в коде, промпт вторичен.
    p = classifier_system_prompt("PB", "uk", track_obligations=True,
                                 obligations_block="- brief (open): бриф")
    assert "bot|client" not in p           # выбор владения из схемы убран
    assert '"owed_by"' not in p            # поля owed_by в JSON-схеме нет
