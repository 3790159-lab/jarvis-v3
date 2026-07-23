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
        def complete(self, system, messages, *, max_tokens, no_thinking=False,
                     uncached_suffix=None, tag=""):
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


# --- видимая деградация: класс сбоя фиксируется, тихого фолбэка нет -----------
# Инцидент volska 2026-07-22 03:21:08: classifier_error БЕЗ строки в логе и с
# ПУСТЫМ control_events.detail — то есть это была парс-деградация (не exception:
# путь exception логируется). Причину нельзя было восстановить. Деградация
# ОБЯЗАНА нести класс сбоя (detail) и оставлять след в логе — бэкстоп страховка,
# а не замена: если классификатор лёг, мы должны это ЗНАТЬ.
import logging  # noqa: E402


def test_degraded_carries_failure_class_in_detail():
    assert parse_classifier_reply("").detail != ""                    # пустой ответ
    assert parse_classifier_reply("извините, не могу").detail != ""   # нет JSON
    assert parse_classifier_reply('{"escalate": true,').detail != ""  # обрезанный JSON
    # валидный ответ — detail пуст (сбоя нет)
    assert parse_classifier_reply(
        '{"escalate": false, "reason": "", "stage_signal": null}').detail == ""


def test_parse_degradation_is_logged_not_silent(caplog):
    llm = FakeLLM(scripted=["это проза, а не JSON"])
    with caplog.at_level(logging.WARNING, logger="chatter.core.classifier"):
        r = classify(llm, playbook="p", language="ru", history=HISTORY)
    assert r.degraded is True
    assert any(rec.levelno >= logging.WARNING and "classifier" in rec.name
               for rec in caplog.records), "парс-деградация ушла в тишину — нет WARNING"


def test_classify_exception_carries_detail():
    class BoomLLM:
        def complete(self, system, messages, *, max_tokens, no_thinking=False,
                     uncached_suffix=None, tag=""):
            raise RuntimeError("network down")
    r = classify(BoomLLM(), playbook="p", language="ru", history=HISTORY)
    assert r.degraded is True
    assert "network down" in r.detail or "RuntimeError" in r.detail


def test_note_classifier_error_persists_detail():
    s = Store(":memory:")
    note_classifier_error(s, now=1000.0, detail="нет JSON-объекта в ответе")
    rows = list(s._conn.execute(
        "SELECT detail FROM control_events WHERE kind='classifier_error'"))
    assert rows and rows[0][0] == "нет JSON-объекта в ответе"


# --- инцидент 2026-07-22: sonnet-5 душил классификатор невидимым thinking ----

def test_classify_disables_thinking_for_the_llm_call():
    """volska на sonnet-5: у модели thinking включён ПО УМОЛЧАНИЮ при опущенном
    параметре, и весь max_tokens=200 сгорал на невидимый thinking-блок ->
    пустой/обрезанный JSON -> деградация на КАЖДОМ сообщении (эскалации тянул
    keyword-бэкстоп). Классификатор обязан явно глушить thinking."""
    from chatter.core.classifier import classify
    from chatter.core.llm import FakeLLM as _F
    llm = _F(scripted=['{"escalate": false, "reason": "", "stage_signal": null}'])
    classify(llm, playbook="p", language="uk",
             history=[{"role": "user", "text": "привіт"}])
    assert llm.calls[0]["no_thinking"] is True


def test_anthropic_llm_sends_thinking_disabled_to_the_sdk():
    """Проверка на уровне провода: no_thinking=True обязан дойти до
    messages.create как thinking={"type": "disabled"} (обе прод-модели --
    sonnet-5 и haiku-4-5 -- принимают его, проверено живьём 22.07)."""
    from chatter.core.llm import AnthropicLLM

    calls = {}

    class _Resp:
        content = []

    class _Messages:
        def create(self, **kw):
            calls.clear()
            calls.update(kw)
            return _Resp()

    class _Client:
        messages = _Messages()

    llm = AnthropicLLM.__new__(AnthropicLLM)
    llm._client = _Client()
    llm._model = "m"
    llm._usage_sink = None

    llm.complete("s", [], max_tokens=10, no_thinking=True)
    assert calls["thinking"] == {"type": "disabled"}

    llm.complete("s", [], max_tokens=10)
    assert "thinking" not in calls   # дефолт модели не трогаем
