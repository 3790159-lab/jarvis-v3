# -*- coding: utf-8 -*-
"""Фаза 1 арки «кэш классификатора»: стабильный префикс + изменчивый хвост.

Диагноз (спека 2026-07-25 §1): профиль и блок обязательств сидели В СЕРЕДИНЕ
кэшируемого system-блока и убивали кэш вместе со всем хвостом инструкций —
1 попадание из 22 после 2026-07-23 18:23. Раскладка A1: стабильное (98%
системы) идёт под cache_control, изменчивое — вторым system-блоком.

ГЛАВНЫЙ тест здесь — `test_stable_prefix_is_byte_identical_*`: он и есть
машинный сторож контракта «всё, что меняется чаще раза в диалог, живёт ПОСЛЕ
breakpoint'а». Регрессия 23.07 прошла молча именно потому, что такого сторожа
не было: тесты зелёные, поведение бота верное, счёт вырос втрое.
"""
from __future__ import annotations

import pytest

from chatter.core.classifier import (
    _RETRY_NUDGE, classifier_stable_prefix, classifier_system_prompt,
    classifier_volatile_suffix, classify,
)

PLAYBOOK = "ПЛЕЙБУК: вилка 300-400$, ребрендинг 200$."
HISTORY = [{"role": "user", "text": "привіт"}]
JSON_OK = '{"escalate": false, "reason": "r", "profile": null, "stage_signal": "engaged"}'


class _RecordingLLM:
    """Пишет РОВНО то, что ушло в complete(): system отдельно от suffix.
    FakeLLM их склеивает для обратной совместимости, а нам нужен именно первый
    блок — он и есть кэшируемый префикс."""

    def __init__(self, replies=None):
        self.replies = list(replies or [JSON_OK])
        self.calls: list[dict] = []
        self.last_stop_reason = None

    def complete(self, system, messages, *, max_tokens, no_thinking=False,
                 uncached_suffix=None, tag=""):
        self.calls.append({"system": system, "suffix": uncached_suffix,
                           "no_thinking": no_thinking, "tag": tag,
                           "messages": messages})
        return self.replies.pop(0) if self.replies else JSON_OK


@pytest.fixture()
def cache_on(monkeypatch):
    monkeypatch.setenv("CHATTER_CLASSIFIER_CACHE", "1")


@pytest.fixture()
def cache_off(monkeypatch):
    monkeypatch.delenv("CHATTER_CLASSIFIER_CACHE", raising=False)


def _classify(llm, **kw):
    return classify(llm, playbook=PLAYBOOK, language="uk", history=HISTORY, **kw)


# ── СТОРОЖ КОНТРАКТА ─────────────────────────────────────────────────────────


def test_stable_prefix_is_byte_identical_across_different_profiles(cache_on):
    """Профиль меняется почти каждый ход — префикс не смеет шевельнуться."""
    a, b = _RecordingLLM(), _RecordingLLM()
    _classify(a, profile="Чайна Гора, лого з нуля 300-400$")
    _classify(b, profile="Інший клієнт, ребрендинг 200$, чекає рахунок")
    assert a.calls[0]["system"] == b.calls[0]["system"]
    assert a.calls[0]["suffix"] != b.calls[0]["suffix"], "разница обязана быть в хвосте"


def test_stable_prefix_is_byte_identical_across_different_obligations(cache_on):
    a, b = _RecordingLLM(), _RecordingLLM()
    _classify(a, track_obligations=True, obligations_block="- brief: open")
    _classify(b, track_obligations=True,
              obligations_block="- brief ✓ delivered\n- owner_write ✓ delivered")
    assert a.calls[0]["system"] == b.calls[0]["system"]
    assert a.calls[0]["suffix"] != b.calls[0]["suffix"]


def test_stable_prefix_carries_no_volatile_text(cache_on):
    """Негативная проверка: ни профиля, ни блока обязательств в префиксе нет —
    иначе «байт-в-байт» можно было бы пройти, забыв их вычистить."""
    llm = _RecordingLLM()
    _classify(llm, profile="СЕКРЕТНЫЙ-ПРОФИЛЬ-МАРКЕР", track_obligations=True,
              obligations_block="МАРКЕР-ОБЯЗАТЕЛЬСТВА")
    prefix, suffix = llm.calls[0]["system"], llm.calls[0]["suffix"]
    assert "СЕКРЕТНЫЙ-ПРОФИЛЬ-МАРКЕР" not in prefix
    assert "МАРКЕР-ОБЯЗАТЕЛЬСТВА" not in prefix
    assert "СЕКРЕТНЫЙ-ПРОФИЛЬ-МАРКЕР" in suffix
    assert "МАРКЕР-ОБЯЗАТЕЛЬСТВА" in suffix


def test_stable_prefix_keeps_playbook_schema_and_antisample(cache_on):
    """Кэшируем именно то, ради чего всё затевалось: плейбук — 77% системы."""
    llm = _RecordingLLM()
    _classify(llm, profile="п", track_obligations=True, obligations_block="о")
    prefix = llm.calls[0]["system"]
    assert PLAYBOOK in prefix
    assert '"escalate"' in prefix           # JSON-схема
    assert "НЕПРАВИЛЬНО" in prefix          # анти-образец (роль-граница)
    assert "obligations" in prefix          # схема слота


def test_instructions_forward_reference_the_tail(cache_on):
    """Блоки переехали в конец — инструкции обязаны на это указывать, иначе
    модель ищет профиль там, где его больше нет."""
    prefix = classifier_stable_prefix(PLAYBOOK, "uk", track_obligations=True)
    assert "кінці" in prefix or "конце" in prefix


# ── byte-identical при выключенном флаге ─────────────────────────────────────


def test_flag_off_sends_legacy_prompt_byte_for_byte(cache_off):
    llm = _RecordingLLM()
    _classify(llm, profile="Чайна Гора", track_obligations=True,
              obligations_block="- brief: open")
    legacy = classifier_system_prompt(
        PLAYBOOK, "uk", profile="Чайна Гора", track_obligations=True,
        obligations_block="- brief: open")
    assert llm.calls[0]["system"] == legacy
    assert llm.calls[0]["suffix"] is None


def test_flag_off_keeps_volatile_inside_the_cached_block(cache_off):
    """Явно фиксируем СТАРОЕ (плохое) поведение как ветку отката: профиль
    внутри первого блока. Если это перестанет быть так — откат сломан."""
    llm = _RecordingLLM()
    _classify(llm, profile="МАРКЕР")
    assert "МАРКЕР" in llm.calls[0]["system"]


def test_prefix_and_suffix_concatenate_to_the_same_content(cache_on):
    """Содержание промпта не теряется при переезде: все смысловые куски на
    месте, меняется только порядок и граница блоков."""
    full_on = (classifier_stable_prefix(PLAYBOOK, "uk", track_obligations=True)
               + classifier_volatile_suffix("Чайна Гора", "- brief: open",
                                            track_obligations=True))
    legacy = classifier_system_prompt(PLAYBOOK, "uk", profile="Чайна Гора",
                                      track_obligations=True,
                                      obligations_block="- brief: open")
    for chunk in (PLAYBOOK, "Чайна Гора", "- brief: open", "НЕПРАВИЛЬНО",
                  '"stage_signal"'):
        assert chunk in legacy and chunk in full_on


# ── совместимость с ретраем и no_thinking ────────────────────────────────────


def test_retry_suffix_carries_both_volatile_and_nudge(cache_on):
    """uncached_suffix был ЗАНЯТ нуджем — коллизия слота. Нудж дописывается
    к изменчивому хвосту, а не вытесняет его."""
    llm = _RecordingLLM(replies=["это не JSON, а реплика клиенту", JSON_OK])
    res = _classify(llm, profile="Чайна Гора")
    assert res.retried and not res.degraded
    assert len(llm.calls) == 2
    retry = llm.calls[1]
    assert "Чайна Гора" in retry["suffix"], "хвост потерян на повторе"
    assert _RETRY_NUDGE in retry["suffix"]
    assert retry["tag"] == "classifier_retry"


def test_retry_keeps_the_cached_prefix_untouched(cache_on):
    """Смысл фикса: повтор платит ~cache-read, а не вторую полную запись."""
    llm = _RecordingLLM(replies=["мусор", JSON_OK])
    _classify(llm, profile="Чайна Гора")
    assert llm.calls[0]["system"] == llm.calls[1]["system"]


def test_no_thinking_stays_on_both_calls(cache_on):
    """Инцидент 2026-07-22: без disabled sonnet-5 съедает бюджет мышлением."""
    llm = _RecordingLLM(replies=["мусор", JSON_OK])
    _classify(llm, profile="п")
    assert all(c["no_thinking"] is True for c in llm.calls)


def test_history_still_goes_to_messages_not_into_the_prompt(cache_on):
    """Опция B (кэш истории) в MVP не входит — история остаётся в messages."""
    llm = _RecordingLLM()
    _classify(llm, profile="п")
    assert llm.calls[0]["messages"] == [{"role": "user", "content": "привіт"}]
    assert "привіт" not in llm.calls[0]["system"]


# ── слот обязательств выключен ───────────────────────────────────────────────


def test_without_obligations_tracking_suffix_has_profile_only(cache_on):
    llm = _RecordingLLM()
    _classify(llm, profile="Чайна Гора")
    suffix = llm.calls[0]["suffix"]
    assert "Чайна Гора" in suffix
    assert "ЗОБОВ'ЯЗАННЯ" not in suffix
    assert "ЗОБОВ'ЯЗАННЯ" not in llm.calls[0]["system"]


def test_empty_profile_still_produces_a_suffix(cache_on):
    """profile=None — обычное состояние нового лида. Хвост обязан существовать
    (с «(порожній)»), иначе первый ход диалога уедет по другой ветке кода."""
    llm = _RecordingLLM()
    _classify(llm, profile=None)
    assert llm.calls[0]["suffix"]
    assert "порожн" in llm.calls[0]["suffix"]
