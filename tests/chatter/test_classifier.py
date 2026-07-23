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


# =============================================================================
# Инцидент volska 2026-07-23 (17:03:45 и 18:28:24): НОВЫЙ режим отказа.
# Классификатор вернул не мусор и не пустоту, а ТЕКСТ РЕПЛИКИ ПРОДАВЦА:
#   «Звучить дуже гармонійно для чайного бренду 🙂 Зелено-бежева спокійна…»
# То есть спутал свою роль с ролью Ольги. 2 сбоя из 14 вызовов за сутки (14%).
# Цена выросла: от классификатора теперь зависит и профиль лида — череда
# сбоев тихо замораживает память, бот выглядит помнящим, но помнит позавчера.
# =============================================================================
from chatter.core.classifier import classifier_system_prompt  # noqa: E402


def test_prompt_draws_role_boundary_against_writing_the_reply():
    """Промпт обязан явно разводить роли: классификатор НЕ участник диалога,
    его ответ читает ПРОГРАММА, а не человек."""
    low = classifier_system_prompt("плейбук", "uk").casefold()
    assert "не участник диалога" in low, "нет запрета быть участником диалога"
    assert "не отвечаешь клиенту" in low, "нет запрета отвечать клиенту"
    assert "программа" in low, "не сказано, что ответ читает программа"


def test_prompt_carries_anti_example_of_the_real_failure():
    """Анти-образец: показать НЕПРАВИЛЬНЫЙ ответ (живая реплика с эмодзи —
    ровно то, что пришло 17:03 и 18:28), а не только описывать правильный."""
    prompt = classifier_system_prompt("плейбук", "uk")
    low = prompt.casefold()
    assert "неправильно" in low, "нет помеченного анти-образца"
    assert "🙂" in prompt, "анти-образец не похож на живую реплику из инцидента"


def test_reply_text_instead_of_json_is_degraded_with_its_class():
    """Регресс-замок на прозу (фиксирует поведение, вскрытое инцидентом):
    текст реплики -> деградация, НЕ эскалация, класс сбоя виден в detail
    вместе с сырым началом ответа — иначе инцидент опять не диагностировать."""
    raw = ("Звучить дуже гармонійно для чайного бренду 🙂 Зелено-бежева "
           "спокійна палітра добре працює на упаковці")
    r = parse_classifier_reply(raw)
    assert r.degraded is True
    assert r.escalate is False
    assert r.profile is None, "из прозы нельзя вытащить профиль — память не трогаем"
    assert "нет JSON" in r.detail
    assert "гармонійно" in r.detail, "в detail нет сырого ответа для форензики"


# --- ретрай: один повтор перед деградацией ----------------------------------

def test_invalid_json_triggers_exactly_one_retry_that_saves_the_turn():
    llm = FakeLLM(scripted=[
        "Звучить дуже гармонійно для чайного бренду 🙂",              # спутал роль
        '{"escalate": false, "reason": "цікавиться", "stage_signal": "engaged"}',
    ])
    r = classify(llm, playbook="p", language="uk", history=HISTORY)
    assert r.degraded is False, "ретрай не спас ход"
    assert r.retried is True, "факт ретрая не виден вызывающей стороне"
    assert r.stage_signal == "engaged"
    assert len(llm.calls) == 2


def test_retry_keeps_cached_prefix_and_puts_correction_out_of_band():
    """Коррекция уходит uncached_suffix'ом (system-блок ПОСЛЕ cache-брейкпоинта),
    а не сообщением в диалог: лишняя user-реплика усилила бы ровно ту путаницу
    ролей, которую мы чиним. Кэш-префикс при этом цел — ретрай почти бесплатен."""
    llm = FakeLLM(scripted=[
        "не JSON",
        '{"escalate": false, "reason": "", "stage_signal": null}',
    ])
    classify(llm, playbook="ПЛЕЙБУК-МАРКЕР", language="uk", history=HISTORY)
    first, second = llm.calls
    assert second["messages"] == first["messages"], "ретрай подменил диалог"
    assert first["uncached_suffix"] is None
    assert second["uncached_suffix"], "коррекции нет"
    assert "JSON" in second["uncached_suffix"]
    assert second["system"].startswith(first["system"]), "кэш-префикс сломан"
    assert second["tag"] == "classifier_retry", "стоимость ретрая не отделима в llm_usage"
    assert second["no_thinking"] is True


def test_second_failure_degrades_and_never_retries_twice():
    llm = FakeLLM(scripted=["реплика 🙂", "знову реплика 🙂"])
    r = classify(llm, playbook="p", language="uk", history=HISTORY)
    assert r.degraded is True
    assert r.retried is True
    assert len(llm.calls) == 2, "ретрай не одноразовый"


def test_happy_path_does_not_retry():
    llm = FakeLLM(scripted=['{"escalate": false, "reason": "", "stage_signal": null}'])
    r = classify(llm, playbook="p", language="uk", history=HISTORY)
    assert r.retried is False
    assert len(llm.calls) == 1


def test_no_retry_when_llm_call_itself_fell():
    """Упавший вызов — не «модель спутала роль», а сеть/ключ. Повтор здесь
    удваивает задержку хода и чинит редко: деградируем сразу."""
    calls = []

    class BoomLLM:
        def complete(self, system, messages, *, max_tokens, no_thinking=False,
                     uncached_suffix=None, tag=""):
            calls.append(tag)
            raise RuntimeError("network down")

    r = classify(BoomLLM(), playbook="p", language="uk", history=HISTORY)
    assert r.degraded is True
    assert r.retried is False
    assert len(calls) == 1


def test_no_retry_when_answer_was_truncated():
    """Обрезка по max_tokens — не путаница ролей, а маленький бюджет: повтор
    обрежет так же. Деградируем сразу с прежним диагнозом."""
    llm = FakeLLM(scripted=['{"escalate": false, "reason": "оч'])
    llm.scripted_stop_reasons = ["max_tokens"]
    r = classify(llm, playbook="p", language="uk", history=HISTORY)
    assert r.degraded is True
    assert r.retried is False
    assert len(llm.calls) == 1
    assert "обрезан" in r.detail


# =============================================================================
# ВИДИМОСТЬ (2026-07-23). Сбои копились в control_events, которую никто не
# читает: порог был «> 5 за 24ч», а фактический режим — 2 сбоя за сутки, то
# есть алерт не сработал бы НИКОГДА. Плюс от классификатора теперь зависит
# профиль: череда пропусков по ОДНОМУ контакту тихо морозит память лида.
# =============================================================================
from chatter.core.classifier import (  # noqa: E402
    classifier_failure_count, note_classifier_recovered,
    note_profile_miss, profile_miss_streak, reset_profile_miss,
)


def test_recovered_by_retry_still_counts_as_a_failure():
    """Ход, спасённый ретраем, — всё равно сбой модели. Если не считать его,
    после включения ретрая статистика обнулится и порог не сработает никогда."""
    s = Store(":memory:")
    note_classifier_error(s, now=1000.0, detail="нет JSON")
    note_classifier_recovered(s, now=1000.0, detail="нет JSON (спасён ретраем)")
    assert classifier_failure_count(s, now=1000.0, window_seconds=86400) == 2


def test_alert_fires_at_the_threshold_not_strictly_above_it():
    """Порог = «столько сбоев за сутки → алерт», а не «строго больше».
    Со старым `>` порог 2 требовал 3 сбоя — ровно на этом двухсбойные сутки
    07-23 и остались невидимыми."""
    s = Store(":memory:")
    note_classifier_error(s, now=1000.0)
    assert classifier_degraded(s, now=1000.0, window_seconds=86400, threshold=2) is False
    note_classifier_recovered(s, now=1000.0)
    assert classifier_degraded(s, now=1000.0, window_seconds=86400, threshold=2) is True


def test_recovered_events_outside_window_dont_count():
    s = Store(":memory:")
    for _ in range(10):
        note_classifier_recovered(s, now=1000.0)
    assert classifier_failure_count(s, now=100000.0, window_seconds=3600) == 0


def test_failures_carry_contact_id_for_forensics():
    """У сбоев 07-23 contact_id был NULL — нельзя было сказать, чью память
    заморозило. Событие обязано нести контакт."""
    s = Store(":memory:")
    note_classifier_error(s, now=1000.0, detail="нет JSON", contact_id="42:volska")
    note_classifier_recovered(s, now=1000.0, detail="нет JSON", contact_id="42:volska")
    rows = list(s._conn.execute(
        "SELECT contact_id FROM control_events "
        "WHERE kind IN ('classifier_error','classifier_recovered')"))
    assert [r[0] for r in rows] == ["42:volska", "42:volska"]


# --- серия пропущенных обновлений профиля по ОДНОМУ контакту ----------------

def test_profile_miss_streak_counts_per_contact():
    s = Store(":memory:")
    assert note_profile_miss(s, "42:volska", now=1.0) == 1
    assert note_profile_miss(s, "42:volska", now=2.0) == 2
    assert note_profile_miss(s, "77:volska", now=3.0) == 1, "серии контактов слиплись"
    assert profile_miss_streak(s, "42:volska") == 2


def test_healthy_turn_resets_the_streak():
    """Здоровый ход — даже без нового факта (profile: null) — значит память
    НЕ отстаёт: серия обрывается."""
    s = Store(":memory:")
    note_profile_miss(s, "42:volska", now=1.0)
    note_profile_miss(s, "42:volska", now=2.0)
    reset_profile_miss(s, "42:volska")
    assert profile_miss_streak(s, "42:volska") == 0
