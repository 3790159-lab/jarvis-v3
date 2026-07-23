"""Арка «память + стоимость» (спека 2026-07-23-chatter-memory-cost, ОК Даниила
с 3 условиями): окно истории ПО ТОКЕНАМ, профиль лида, расширение
классификатора profile-полем, детект обрезки ответа."""
from __future__ import annotations

import logging

import pytest

from chatter.core.classifier import (
    _CLASSIFIER_MAX_TOKENS, classifier_system_prompt, classify,
    parse_classifier_reply,
)
from chatter.core.llm import FakeLLM
from chatter.core.window import estimate_tokens, select_window
from chatter.storage.db import Store

HISTORY = [{"role": "user", "text": "привіт"}]


# --- условие 2: окно по токенам, не по сообщениям ----------------------------
def _msg(text, role="user"):
    return {"role": role, "text": text}


def test_window_keeps_tail_within_token_budget():
    hist = [_msg(f"повідомлення номер {i} " + "х" * 100) for i in range(50)]
    win = select_window(hist, budget_tokens=500, max_messages=40)
    assert win == hist[-len(win):]          # хвост, порядок сохранён
    assert 0 < len(win) < 50
    assert sum(estimate_tokens(m["text"]) for m in win) <= 500


def test_window_short_messages_capped_by_max_messages():
    """Fallback на количество: море микро-реплик не тащит старьё."""
    hist = [_msg("ок") for _ in range(200)]
    win = select_window(hist, budget_tokens=100_000, max_messages=40)
    assert len(win) == 40


def test_window_always_includes_last_message_even_if_huge():
    """Зафиксировано (условие 2): одно гигантское сообщение само больше
    бюджета — окно всё равно содержит последнее сообщение ЦЕЛИКОМ (не режем
    посреди реплики лида), бюджет в этом случае осознанно превышен."""
    hist = [_msg("старе"), _msg("х" * 30_000)]
    win = select_window(hist, budget_tokens=100, max_messages=40)
    assert len(win) == 1
    assert win[0]["text"] == "х" * 30_000


def test_window_empty_history():
    assert select_window([], budget_tokens=100, max_messages=10) == []


def test_estimate_tokens_cyrillic_calibration():
    """~3 симв/токен для кириллицы (по факту замера: ~140 ток на ~420 симв
    обмена). Оценка должна быть в разумном коридоре, не в разы."""
    text = "х" * 300
    assert 80 <= estimate_tokens(text) <= 160


# --- профиль в БД -------------------------------------------------------------
def test_profile_roundtrip_and_versioning():
    s = Store(":memory:")
    assert s.get_profile("c1") is None
    s.set_profile("c1", "Клієнт: кав'ярня", ts=1.0)
    s.set_profile("c1", "Клієнт: кав'ярня, вилка 700–900 названа", ts=2.0)
    assert s.get_profile("c1") == "Клієнт: кав'ярня, вилка 700–900 названа"
    versions = s._conn.execute(
        "SELECT version FROM contact_profile WHERE contact_id='c1' ORDER BY version"
    ).fetchall()
    assert [v[0] for v in versions] == [1, 2]  # append-only, история жива


def test_profile_replaced_not_duplicated():
    """Условие 3: смена решения = ЗАМЕНА профиля (актуальное значение с
    пометкой), а не два равнозначных факта."""
    s = Store(":memory:")
    s.set_profile("c1", "Бюджет: до 700 $", ts=1.0)
    s.set_profile("c1", "Бюджет: до 1000 $ (раніше до 700 — передумав)", ts=2.0)
    prof = s.get_profile("c1")
    assert "1000" in prof and "передумав" in prof
    # старое значение существует ТОЛЬКО как пометка внутри актуального
    assert prof.count("700") == 1


# --- классификатор: profile-поле ---------------------------------------------
def test_classifier_prompt_carries_profile_and_conflict_rule():
    sp = classifier_system_prompt("плейбук", "uk", profile="Клієнт: кав'ярня")
    assert "Клієнт: кав'ярня" in sp
    assert "profile" in sp
    # условие 3: инструкция «передумав → нове значення з позначкою»
    low = sp.casefold()
    assert "передумав" in low
    assert "рівнозначн" in low or "равнозначн" in low


def test_classifier_prompt_without_profile_still_has_contract():
    sp = classifier_system_prompt("плейбук", "uk")
    assert "profile" in sp


def test_parse_extracts_profile():
    r = parse_classifier_reply(
        '{"escalate": false, "reason": "", "profile": "Клієнт: пекарня", '
        '"stage_signal": null}')
    assert r.profile == "Клієнт: пекарня"
    assert r.degraded is False


def test_parse_profile_null_or_absent_is_none():
    assert parse_classifier_reply(
        '{"escalate": false, "reason": "", "profile": null, "stage_signal": null}'
    ).profile is None
    assert parse_classifier_reply(
        '{"escalate": false, "reason": "", "stage_signal": null}').profile is None


def test_classifier_max_tokens_raised_with_margin():
    """Условие 1: замер 2026-07-23 дал 157–177 ток ответа с профилем; лимит
    обязан держать запас ×2+ (старые 200 были бы впритык)."""
    assert _CLASSIFIER_MAX_TOKENS >= 450


def test_truncated_reply_is_explicit_degradation_not_silent_json():
    """Условие 1: stop_reason=max_tokens → ЯВНАЯ деградация «ответ обрезан»
    (не тихий битый JSON), профиль НЕ применяется."""
    llm = FakeLLM(scripted=['{"escalate": true, "reason": "обрі'])
    llm.scripted_stop_reasons = ["max_tokens"]
    r = classify(llm, playbook="p", language="uk", history=HISTORY)
    assert r.degraded is True
    assert "обрезан" in r.detail or "max_tokens" in r.detail
    assert r.profile is None


def test_truncated_but_valid_looking_json_still_degrades():
    """Даже если обрезанный ответ случайно распарсился — обрезка есть обрезка:
    классификация не должна опираться на неполный ответ."""
    llm = FakeLLM(scripted=['{"escalate": false, "reason": "", "stage_signal": null}'])
    llm.scripted_stop_reasons = ["max_tokens"]
    r = classify(llm, playbook="p", language="uk", history=HISTORY)
    assert r.degraded is True


def test_classify_passes_profile_into_prompt():
    llm = FakeLLM(scripted=['{"escalate": false, "reason": "", "stage_signal": null}'])
    classify(llm, playbook="p", language="uk", history=HISTORY,
             profile="Клієнт: студія йоги")
    assert "Клієнт: студія йоги" in llm.calls[0]["system"]


# --- ПРИЁМКА: диалог → пауза → возврат (требование Даниила) ------------------
def test_acceptance_bot_remembers_lead_after_pause():
    """Лид описал проект сегодня; вернулся завтра. Механизм памяти обязан:
    (1) сохранить профиль из ответа классификатора, (2) на возврате подать
    профиль в промпт brain (проект + договорённости) и классификатора,
    (3) окно — по токенам. «Не переспрашивает сферу» обеспечивает профиль в
    промпте — живую прозу проверяет дрил Даниила «сегодня → завтра»."""
    import random
    from pathlib import Path

    from chatter.config.loader import load_config
    from chatter.core.brain import Brain
    from chatter.core.classifier import classify as real_classify
    from chatter.run import Deps, process_batch

    class _T:  # минимальный транспорт
        def __init__(self):
            self.sent = []

        def send(self, text):
            self.sent.append(text)

        def send_typing(self, on):
            pass

        def set_online(self, on):
            pass

        def read_acknowledge(self):
            pass

    clients = Path(__file__).resolve().parents[2] / "chatter" / "clients"
    cfg = load_config(clients, "demo")
    store = Store(":memory:")
    brain_llm = FakeLLM(scripted=["Вітаю! Розкажіть про задачу?", "З поверненням 🙂"])
    clf_llm = FakeLLM(scripted=[
        # ход 1: классификатор извлёк профиль
        '{"escalate": false, "reason": "", "profile": "Клієнт: кав\'ярня, '
        'айдентика; вилка 700–900 $ названа; домовились: скине бриф", '
        '"stage_signal": "engaged"}',
        # ход 2 (после паузы): без обновлений
        '{"escalate": false, "reason": "", "profile": null, "stage_signal": null}',
    ])
    now = [1_000.0]
    deps = Deps(cfg=cfg, store=store, brain=Brain(brain_llm, cfg),
                rng=random.Random(0), clock=lambda: now[0], sleep=lambda s: None)
    deps.classify = lambda h, profile=None: real_classify(
        clf_llm, playbook=cfg.playbook, language=cfg.settings.language,
        history=h, profile=profile)

    store.get_or_create_contact("lead1")
    process_batch("lead1", ["Ми запускаємо кав'ярню, потрібна айдентика"], _T(), deps)

    # (1) профиль сохранён из ответа классификатора
    prof = store.get_profile("lead1")
    assert prof and "кав'ярня" in prof and "бриф" in prof

    # пауза: следующий день
    now[0] += 86_400.0
    process_batch("lead1", ["Добрий день, це знову я"], _T(), deps)

    # (2) на возврате профиль поехал в ПРОМПТ brain (suffix, кэш системы цел)
    ret_call = brain_llm.calls[-1]
    assert ret_call["uncached_suffix"] and "кав'ярня" in ret_call["uncached_suffix"]
    assert "бриф" in ret_call["uncached_suffix"]
    # ...и в промпт классификатора
    assert "кав'ярня" in clf_llm.calls[-1]["system"]

    # (3) история в вызове — окно (хвост), не обрезана посреди последней реплики
    assert ret_call["messages"][-1]["content"] == "Добрий день, це знову я"


def test_profile_not_written_on_degraded_classifier():
    """Обрезка/мусор классификатора НЕ должны портить профиль (fail-safe)."""
    import random
    from pathlib import Path

    from chatter.config.loader import load_config
    from chatter.core.brain import Brain
    from chatter.core.classifier import classify as real_classify
    from chatter.run import Deps, process_batch

    class _T:
        def __init__(self):
            self.sent = []

        def send(self, text):
            self.sent.append(text)

        def send_typing(self, on):
            pass

        def set_online(self, on):
            pass

        def read_acknowledge(self):
            pass

    clients = Path(__file__).resolve().parents[2] / "chatter" / "clients"
    cfg = load_config(clients, "demo")
    store = Store(":memory:")
    store.set_profile("lead1", "Клієнт: пекарня", ts=1.0)
    clf_llm = FakeLLM(scripted=['{"escalate": false, "profile": "СМІТТЯ-ОБРІЗ'])
    clf_llm.scripted_stop_reasons = ["max_tokens"]
    deps = Deps(cfg=cfg, store=store,
                brain=Brain(FakeLLM(scripted=["ок"]), cfg),
                rng=random.Random(0), clock=lambda: 1000.0, sleep=lambda s: None)
    deps.classify = lambda h, profile=None: real_classify(
        clf_llm, playbook=cfg.playbook, language=cfg.settings.language,
        history=h, profile=profile)
    store.get_or_create_contact("lead1")
    process_batch("lead1", ["привіт"], _T(), deps)
    assert store.get_profile("lead1") == "Клієнт: пекарня"  # не тронут
