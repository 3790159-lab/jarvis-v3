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


def test_owed_by_is_asked_only_for_kind_other():
    # ПЕРЕСМОТРЕНО 2026-07-25 (P17). Было: поля owed_by в схеме нет вовсе —
    # владелец есть инвариант кода (баг Д-10 2026-07-24: client-owed brief не
    # доезжал до brain). Оказалось, у свободной корзины `other` инварианта нет:
    # merge подставлял `bot` по умолчанию, и факт про ход КЛИЕНТА («клієнт ще не
    # оплатив») становился долгом бота и дожимался в промпте.
    # Стало: поле есть, но ТОЛЬКО для other; у канонических видов по-прежнему
    # владеет код. Оба условия держим одним тестом, чтобы «вернуть как было»
    # нельзя было незаметно.
    p = classifier_system_prompt("PB", "uk", track_obligations=True,
                                 obligations_block="- brief (open): бриф")
    assert '"owed_by"' in p                      # поле есть
    assert "ТОЛЬКО для kind=other" in p          # и оно ограничено other
    assert "у остальных видов его НЕ пиши" in p  # канонические — за кодом


def test_brief_closure_criterion_has_material_anti_example():
    # Д-10 T2 2026-07-24: классификатор держал brief open «в ожидании материалов
    # от клиента». Явный анти-пример: вопросы ЗАДАНЫ = delivered, ожидание
    # материала НЕ является открытым долгом бота.
    p = classifier_system_prompt("PB", "uk", track_obligations=True,
                                 obligations_block="- brief (open): бриф")
    assert "материалов от клиента" in p


def test_anti_fragmentation_instruction_present():
    # Д-10 T2: при открытом brief создан дублирующий other → cap ≤5 клонами.
    p = classifier_system_prompt("PB", "uk", track_obligations=True,
                                 obligations_block="- brief (open): бриф")
    assert "дублирующее" in p
