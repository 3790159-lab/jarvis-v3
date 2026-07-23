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


# --- условие 4 (перед мержем): потолок профиля --------------------------------
# Профиль не кэшируется и платится ПОЛНОСТЬЮ на каждом вызове (brain +
# classifier) → его рост = прямой рост стоимости, тот же D1 этажом выше.
class _Transport:
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


def _make_deps(store, clf_llm, brain_replies=("ок",)):
    import random
    from pathlib import Path

    from chatter.config.loader import load_config
    from chatter.core.brain import Brain
    from chatter.core.classifier import classify as real_classify
    from chatter.run import Deps

    clients = Path(__file__).resolve().parents[2] / "chatter" / "clients"
    cfg = load_config(clients, "demo")
    deps = Deps(cfg=cfg, store=store, brain=Brain(FakeLLM(scripted=list(brain_replies)), cfg),
                rng=random.Random(0), clock=lambda: 1000.0, sleep=lambda s: None)
    deps.classify = lambda h, profile=None: real_classify(
        clf_llm, playbook=cfg.playbook, language=cfg.settings.language,
        history=h, profile=profile,
        profile_budget_tokens=cfg.settings.limits.profile_budget_tokens)
    return deps


def test_limits_profile_budget_is_per_client_field():
    """Жёсткий бюджет профиля в токенах живёт в settings.limits (per-client),
    как history_budget_tokens."""
    from chatter.config.loader import _LIMIT_FIELDS, DEFAULT_LIMITS
    assert "profile_budget_tokens" in _LIMIT_FIELDS
    assert DEFAULT_LIMITS.profile_budget_tokens == 250


def test_classifier_prompt_budget_compaction_and_eviction():
    """Инструкция классификатору: профиль переписывается КОМПАКТНО (ужимает,
    а не накапливает), правило вытеснения явное — что выбрасываем первым
    (устаревшие «передумав», закрытые вопросы), что не выбрасываем никогда
    (проект, вилки, договорённости, статус воронки). Симв-лимит в промпте
    ДИНАМИЧЕСКИЙ от бюджета: ⅔ потолка (просим меньше, чем рубим)."""
    sp = classifier_system_prompt("плейбук", "uk", profile="x",
                                  profile_budget_tokens=200)
    assert "400" in sp  # 200 ток × 3 симв/ток × 2/3
    low = sp.casefold()
    assert "ужимай" in low and "накаплива" in low
    assert "первую очередь" in low
    assert "вилк" in low and "договорённост" in low and "воронк" in low


def test_classify_passes_profile_budget_into_prompt():
    llm = FakeLLM(scripted=['{"escalate": false, "reason": "", "stage_signal": null}'])
    classify(llm, playbook="p", language="uk", history=HISTORY,
             profile_budget_tokens=200)
    assert "400" in llm.calls[0]["system"]


def test_bind_classifier_passes_client_profile_budget():
    """Прод-шов: _bind_classifier несёт бюджет ИЗ КОНФИГА клиента в classify.
    Бюджет НЕдефолтный (200 ток → «400 символов»), чтобы тест не проходил
    вакуумно на захардкоженной цифре в промпте."""
    from dataclasses import replace
    from pathlib import Path

    from chatter.config.loader import load_config
    from chatter.telethon_run import _bind_classifier

    clients = Path(__file__).resolve().parents[2] / "chatter" / "clients"
    cfg = load_config(clients, "demo")
    cfg = replace(cfg, settings=replace(
        cfg.settings, limits=replace(cfg.settings.limits, profile_budget_tokens=200)))
    llm = FakeLLM(scripted=['{"escalate": false, "reason": "", "stage_signal": null}'])
    _bind_classifier(llm, cfg)(HISTORY)
    assert "400" in llm.calls[0]["system"]


def test_oversized_profile_not_applied_explicit_degradation():
    """Профиль сверх бюджета: НЕ применяется (старый жив), событие
    classifier_error с классом «бюджет» — явная деградация, НЕ тихая обрезка
    и НЕ тихое применение."""
    from chatter.run import process_batch

    store = Store(":memory:")
    store.set_profile("lead1", "Клієнт: пекарня", ts=1.0)
    huge = "Клієнт: кав'ярня; " + "деталі проекту і зайвий текст " * 60  # >> 250 ток
    clf_llm = FakeLLM(scripted=[
        '{"escalate": false, "reason": "", "profile": "' + huge + '", '
        '"stage_signal": null}'])
    deps = _make_deps(store, clf_llm)
    store.get_or_create_contact("lead1")
    process_batch("lead1", ["привіт"], _Transport(), deps)

    assert store.get_profile("lead1") == "Клієнт: пекарня"  # старый жив
    rows = store._conn.execute(
        "SELECT detail FROM control_events WHERE kind='classifier_error'"
    ).fetchall()
    assert rows and any("бюджет" in (r[0] or "") for r in rows)


def test_long_history_compact_profile_within_budget_keeps_key_facts():
    """Длинная история с множеством фактов → профиль в бюджете применяется и
    несёт ключевое (проект, вилка, договорённость). Живую КОМПАКЦИЮ прозы
    проверяет дрил Даниила — здесь контракт плумбинга: компактный профиль
    проходит, оверсайз (тест выше) рубится."""
    from chatter.run import process_batch

    store = Store(":memory:")
    compact = ("Клієнт: кав'ярня, айдентика; вилка 700–900 $ названа; "
               "домовились: скине бриф; стадія: interested")
    clf_llm = FakeLLM(scripted=[
        '{"escalate": false, "reason": "", "profile": "' + compact + '", '
        '"stage_signal": "interested"}'])
    deps = _make_deps(store, clf_llm)
    store.get_or_create_contact("lead1")
    batch = [f"факт номер {i}: подробиці проєкту, бюджети, терміни" for i in range(30)]
    process_batch("lead1", batch, _Transport(), deps)

    prof = store.get_profile("lead1")
    assert prof == compact
    assert estimate_tokens(prof) <= 250
    for key in ("кав'ярня", "700–900", "бриф"):
        assert key in prof
