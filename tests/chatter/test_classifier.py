from __future__ import annotations

from chatter.core.classifier import (
    ClassifierResult,
    classify,
    parse_classifier_reply,
)
from chatter.core.llm import FakeLLM


HISTORY = [
    {"role": "user", "text": "Хочу забронировать съёмку на выходные, бюджет есть"},
]


def test_parse_valid_json():
    r = parse_classifier_reply(
        '{"escalate": true, "reason": "горячий лид, готов платить", "stage_signal": "hot"}')
    assert isinstance(r, ClassifierResult)
    assert r.escalate is True
    assert r.reason == "горячий лид, готов платить"
    # 'hot' не входит в STAGE_SIGNALS воронки -> coerced to None (см. ниже)
    assert r.degraded is False


def test_parse_strips_code_fences():
    raw = '```json\n{"escalate": false, "reason": "просто спросил", "stage_signal": "engaged"}\n```'
    r = parse_classifier_reply(raw)
    assert r.escalate is False
    assert r.stage_signal == "engaged"
    assert r.degraded is False


def test_parse_finds_json_in_prose():
    raw = 'Вот мой ответ: {"escalate": true, "reason": "оплата", "stage_signal": "interested"} — всё.'
    r = parse_classifier_reply(raw)
    assert r.escalate is True
    assert r.stage_signal == "interested"


def test_garbage_is_degraded_not_escalated():
    r = parse_classifier_reply("извините, не могу")
    assert r.degraded is True
    assert r.escalate is False       # DEV-18: деградация НЕ эскалирует
    assert r.stage_signal is None


def test_empty_is_degraded():
    r = parse_classifier_reply("")
    assert r.degraded is True
    assert r.escalate is False


def test_out_of_vocab_stage_signal_coerced_to_none():
    r = parse_classifier_reply(
        '{"escalate": false, "reason": "x", "stage_signal": "banana"}')
    assert r.degraded is False
    assert r.stage_signal is None    # неизвестный сигнал воронки -> None, не падаем


def test_classify_happy_path():
    llm = FakeLLM(scripted=[
        '{"escalate": true, "reason": "готов бронировать", "stage_signal": "interested"}'])
    r = classify(llm, playbook="воронка", language="ru", history=HISTORY)
    assert r.escalate is True
    assert r.stage_signal == "interested"
    assert r.degraded is False


def test_classify_llm_raises_is_degraded_never_raises():
    class BoomLLM:
        def complete(self, system, messages, *, max_tokens):
            raise RuntimeError("network down")
    r = classify(BoomLLM(), playbook="p", language="ru", history=HISTORY)
    assert r.degraded is True
    assert r.escalate is False       # упавший классификатор не эскалирует


def test_classify_passes_playbook_into_system_prompt():
    llm = FakeLLM(scripted=['{"escalate": false, "reason": "", "stage_signal": null}'])
    classify(llm, playbook="СЕКРЕТНЫЙ-ПЛЕЙБУК-МАРКЕР", language="ru", history=HISTORY)
    assert "СЕКРЕТНЫЙ-ПЛЕЙБУК-МАРКЕР" in llm.calls[0]["system"]


# --- degradation counter + alert decision (спека §6) ------------------------

from chatter.core.classifier import (  # noqa: E402
    classifier_degraded, note_classifier_error,
)
from chatter.storage.db import Store  # noqa: E402


def test_below_threshold_not_degraded():
    s = Store(":memory:")
    for _ in range(3):
        note_classifier_error(s, now=1000.0)
    assert classifier_degraded(s, now=1000.0, window_seconds=3600, threshold=5) is False


def test_above_threshold_is_degraded():
    s = Store(":memory:")
    for _ in range(6):
        note_classifier_error(s, now=1000.0)
    assert classifier_degraded(s, now=1000.0, window_seconds=3600, threshold=5) is True


def test_old_errors_outside_window_dont_count():
    s = Store(":memory:")
    for _ in range(10):
        note_classifier_error(s, now=1000.0)          # старые
    note_classifier_error(s, now=100000.0)            # одна свежая
    assert classifier_degraded(s, now=100000.0, window_seconds=3600, threshold=5) is False
