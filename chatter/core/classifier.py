"""Классификатор эскалации (арка 3B, спека §4 слой 2): ОТДЕЛЬНЫЙ дешёвый
LLM-вызов, шов основного brain-ответа не трогаем. Возвращает
`{escalate, reason, stage_signal}`.

Fail-safe (§6, DEV-18): любой сбой вызова ИЛИ мусор в ответе → `degraded=True,
escalate=False`. Мы НЕ эскалируем на деградации (иначе классификатор, отвечающий
мусором, штормил бы владельца) и НЕ глотаем сбой молча — вызывающая сторона
считает деградации и при превышении порога алертит владельца.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, replace

from chatter.core.window import _CHARS_PER_TOKEN

log = logging.getLogger("chatter.core.classifier")

# Допустимый словарь сигналов воронки — РОВНО те, что понимает
# conversation.next_state (сигналы переходов, не состояния). 'hot' — состояние,
# не сигнал, поэтому сюда не входит и будет приведён к None.
STAGE_SIGNALS = frozenset(
    {"engaged", "interested", "needs_human", "unknown_info", "bought", "ghosted"})

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)

# Условие 1 арки «память»: замер 2026-07-23 на реальной истории дал 157–177
# ток ответа С профилем (все JSON-ok) — старые 200 были впритык. 500 = запас
# ×2.5; обрезка при этом лимите = аномалия и ЯВНАЯ деградация (см. classify).
_CLASSIFIER_MAX_TOKENS = 500

# Условие 4: потолок профиля. Дефолт = DEFAULT_LIMITS.profile_budget_tokens
# (прод всегда передаёт значение из settings.limits клиента через
# _bind_classifier; дефолт здесь — для прямых вызовов и тестов).
_PROFILE_BUDGET_TOKENS = 250


@dataclass(frozen=True)
class ClassifierResult:
    escalate: bool
    reason: str
    stage_signal: str | None
    degraded: bool = False
    # Обновлённый ПОЛНЫЙ профиль лида (None = «нового нічого немає»).
    # Применяется вызывающей стороной ТОЛЬКО при degraded=False.
    profile: str | None = None
    # Класс сбоя при degraded=True (пусто, когда всё хорошо). Инцидент volska
    # 2026-07-22: деградация без причины не диагностируется — теперь несём её.
    detail: str = ""
    # Первый ответ был невалиден и мы его перезапросили (инцидент 2026-07-23).
    # True и при спасённом ходе, и при провалившемся ретрае: вызывающая сторона
    # считает ЛЮБОЙ такой ход как сбой модели — иначе «спасённые» отказы
    # исчезают из статистики и порог алерта никогда не срабатывает.
    retried: bool = False
    # Обновления слота обязательств (спека 2026-07-24 §4): кортеж dict-ов
    # {kind, owed_by, status, detail, slug?}. Применяется ВЫЗЫВАЮЩЕЙ стороной
    # ТОЛЬКО при degraded=False и включённом флаге CHATTER_OBLIGATIONS_SLOT.
    # Пусто → изменений слота нет. Валидацию делает merge_obligations.
    obligations: tuple = ()


def _degraded(detail: str = "") -> ClassifierResult:
    return ClassifierResult(
        escalate=False, reason="", stage_signal=None, degraded=True, detail=detail)


# Корректирующая заметка для ЕДИНСТВЕННОГО повтора (инцидент 2026-07-23).
# Уходит `uncached_suffix`ом — отдельным system-блоком ПОСЛЕ cache_control-
# брейкпоинта: кэш-префикс (плейбук + профиль) остаётся валидным, поэтому
# повтор стоит примерно как cache-read, а не как новый вызов. Сообщением в
# `messages` её слать НЕЛЬЗЯ: лишняя реплика в диалоге усиливает ровно ту
# путаницу ролей, которую мы чиним.
_RETRY_NUDGE = (
    "ПОВТОР. Твой прошлый ответ не был JSON-объектом — похоже, ты начал писать "
    "реплику клиенту. Это не твоя роль. Ответь ЗАНОВО одним JSON-объектом "
    "{\"escalate\": …, \"reason\": …, \"profile\": …, \"stage_signal\": …} "
    "и больше ничем: ни приветствия, ни пояснений, ни текста реплики."
)


def parse_classifier_reply(raw: str) -> ClassifierResult:
    """Терпимый парсер ответа классификатора. Снимает ```-ограждения, находит
    первый {...}, json.loads. На ЛЮБОМ сбое → деградация (не эскалируем) С
    УКАЗАНИЕМ КЛАССА сбоя в detail. Неизвестный stage_signal приводится к None,
    но escalate/reason всё равно честно читаются."""
    text = (raw or "").strip()
    if not text:
        return _degraded("пустой ответ классификатора")
    m = _JSON_OBJECT_RE.search(text)
    if not m:
        return _degraded(f"нет JSON-объекта в ответе: {text[:80]!r}")
    try:
        data = json.loads(m.group(0))
    except (json.JSONDecodeError, ValueError) as e:
        return _degraded(f"невалидный JSON ({e}): {m.group(0)[:80]!r}")
    if not isinstance(data, dict):
        return _degraded(f"JSON не объект, а {type(data).__name__}")
    signal = data.get("stage_signal")
    if signal not in STAGE_SIGNALS:
        signal = None
    raw_profile = data.get("profile")
    profile = (str(raw_profile).strip() or None) if isinstance(raw_profile, str) else None
    raw_obl = data.get("obligations")
    obligations = (tuple(d for d in raw_obl if isinstance(d, dict))
                   if isinstance(raw_obl, list) else ())
    return ClassifierResult(
        escalate=bool(data.get("escalate", False)),
        reason=str(data.get("reason", "") or ""),
        stage_signal=signal,
        degraded=False,
        profile=profile,
        obligations=obligations,
    )


def classifier_system_prompt(playbook: str, language: str,
                             profile: str | None = None,
                             profile_budget_tokens: int = _PROFILE_BUDGET_TOKENS,
                             *, track_obligations: bool = False,
                             obligations_block: str = "") -> str:
    signals = ", ".join(sorted(STAGE_SIGNALS))
    # Просим ⅔ от жёсткого потолка: модель не считает символы точно, запас
    # между просьбой и рубежом (run.py не применяет профиль сверх потолка)
    # держит нормальную работу вне зоны отсечения.
    profile_chars = profile_budget_tokens * _CHARS_PER_TOKEN * 2 // 3
    # Слот обязательств (спека 2026-07-24 §4) — ТОЛЬКО при track_obligations.
    # Иначе obl_section и schema_obl пустые → промпт байт-в-байт как до арки
    # (приёмка «flag off = поведение как сейчас»).
    obl_section = ""
    schema_obl = ""
    if track_obligations:
        # owed_by НЕ в схеме: для brief/examples/recalc это инвариант кода
        # (filter_model_updates форсит bot), а не выбор модели — иначе client-owed
        # brief не доедет до brain (баг Д-10 2026-07-24). Код владеет полем.
        schema_obl = (', "obligations": [{"kind": "brief|examples|recalc|'
                      'owner_write|other", "status": '
                      '"open|delivered|cancelled", "detail": "<=80"}]')
        obl_section = (
            "=== ВІДКРИТІ ЗОБОВ'ЯЗАННЯ (поточні; онови статуси) ===\n"
            f"{obligations_block or '(порожньо)'}\n\n"
            "ЗОБОВ'ЯЗАННЯ: следи, что бот ДОЛЖЕН лиду (обещанный бриф, примеры "
            "работ, пересчёт цены, «керівниця напише») и что должен лид. Верни "
            "массив obligations — по объекту на КАЖДОЕ активное обязательство с "
            "актуальным статусом. delivered ставь по ВЫПОЛНЕНИЮ ФУНКЦИИ, а НЕ по "
            "упоминанию слова: brief=квалифицирующие вопросы по существу ЗАДАНЫ "
            "ботом (сфера/обсяг/стиль/символ-vs-текст/референси) — тогда brief "
            "СРАЗУ delivered. НЕ держи brief open в ожидании материалов от "
            "клиента: пока лид не прислал референси/лого — это НЕ открытое "
            "обязательство БОТА (бот свою функцию выполнил, задав вопросы; "
            "прислать материал — долг клиента, его в этот слот не пишем). "
            "examples=дана ссылка на портфоліо; recalc=названа сумма/зафиксирован "
            "запрос. owner_write ты только СОЗДАЁШЬ (open), когда обещано "
            "«керівниця напише»; закрывать или отменять owner_write НЕЛЬЗЯ — его "
            "статус ведёт ПРОГРАММА по факту доставленной карточки. НЕ создавай "
            "other, дублирующее по смыслу уже существующее обязательство другого "
            "вида: уточнение по брифу — это часть brief, а НЕ отдельный other. "
            "Обязательство БЕЗ изменений можно не возвращать. Это ОТДЕЛЬНЫЙ "
            "структурный список — НЕ ужимай его при сжатии профиля. Нет "
            "обязательств — [].\n\n")
    return (
        "Ты — тихий классификатор диалога воронки продаж. Тебя НЕ видит клиент. "
        # Ролевая граница (инцидент volska 2026-07-23 17:03 и 18:28: модель
        # вернула текст реплики продавца вместо JSON — спутала себя с Ольгой).
        # Переписка в messages — это УЛИКА, а не разговор с тобой.
        "Ты НЕ участник диалога и ты НЕ отвечаешь клиенту: реплики пишет другая "
        "модель, а твой ответ читает ПРОГРАММА и разбирает его как JSON. "
        "Переписка ниже — материал для разбора, а не обращение к тебе. "
        "По переписке реши: (1) нужно ли ПРЯМО СЕЙЧАС передать диалог живому "
        "владельцу (горячий лид, готов платить/бронировать, жалоба, нестандартный "
        "запрос вне плейбука); (2) на какой стадии воронки диалог; (3) обнови "
        "профиль клиента.\n\n"
        f"=== ПЛЕЙБУК ВОРОНКИ ===\n{playbook}\n\n"
        f"=== ПРОФИЛЬ КЛИЕНТА (из прошлых разговоров) ===\n"
        f"{profile or '(порожній)'}\n\n"
        "ПРОФИЛЬ: если из переписки узнал НОВЫЕ факты (кто клиент, сфера, "
        "проект, какие вилки цен уже названы, договорённости, возражения, "
        "даты) — верни в поле profile ПОЛНЫЙ обновлённый профиль. Профиль "
        f"переписывается КОМПАКТНО, не длиннее {profile_chars} символов: "
        "ужимай, а не накапливай. Если не влезает, выбрасывай В ПЕРВУЮ "
        "ОЧЕРЕДЬ: устаревшие пометки «(раніше X — передумав)» (оставь только "
        "актуальное значение) и закрытые вопросы. НИКОГДА не выбрасывай: кто "
        "клиент и его проект, названные вилки цен, договорённости, статус "
        "воронки. Если клиент ПЕРЕДУМАЛ (бюджет, сроки, объём) — "
        "актуальное значение с пометкой «(раніше X — передумав)»; старое НЕ "
        "держи как равнозначное. Если нового ничего нет и профиль актуален — "
        "profile: null.\n\n"
        + obl_section +
        "Ответь СТРОГО одним компактным JSON-объектом, без пояснений и без "
        "markdown:\n"
        '{"escalate": true|false, "reason": "<=120 символов, что хочет лид / '
        'почему эскалация>", "profile": "<полный обновлённый профиль|null>", '
        '"stage_signal": "<' + signals + '|null>"' + schema_obl + '}\n'
        f"reason и profile пиши на языке диалога ({language}).\n\n"
        # Анти-образец: описания правильного формата оказалось мало — модель
        # дважды за сутки вернула живую реплику. Показываем сам провал.
        "НЕПРАВИЛЬНО (так отвечать НЕЛЬЗЯ — это реплика клиенту, а не разбор):\n"
        "Звучить дуже гармонійно для чайного бренду 🙂 Зелено-бежева палітра "
        "добре працює на упаковці\n"
        "ПРАВИЛЬНО:\n"
        '{"escalate": false, "reason": "обговорює палітру для чайного бренду", '
        '"profile": "чайний бренд, обрана зелено-бежева палітра", '
        '"stage_signal": "engaged"}\n'
        "Если тянет написать связный текст на языке диалога — это признак, что "
        "ты перепутал роль. Первый символ твоего ответа — «{», последний — «}»."
    )


def build_classifier_messages(history: list[dict]) -> list[dict]:
    return [{"role": m["role"], "content": m["text"]} for m in history]


def classify(llm, *, playbook: str, language: str, history: list[dict],
             profile: str | None = None,
             profile_budget_tokens: int = _PROFILE_BUDGET_TOKENS,
             track_obligations: bool = False, obligations_block: str = "") -> ClassifierResult:
    """Один дешёвый вызов + ОДИН повтор при невалидном JSON.
    НИКОГДА не бросает: сбой вызова → деградация (§6)."""
    system = classifier_system_prompt(
        playbook, language, profile=profile,
        profile_budget_tokens=profile_budget_tokens,
        track_obligations=track_obligations, obligations_block=obligations_block)
    messages = build_classifier_messages(history)

    def _call(nudge: str | None) -> str:
        return llm.complete(
            system, messages,
            max_tokens=_CLASSIFIER_MAX_TOKENS,
            # Без этого sonnet-5 (thinking по умолчанию) сжигает весь бюджет
            # на невидимое мышление -> пустой JSON -> деградация (2026-07-22).
            no_thinking=True,
            uncached_suffix=nudge,
            tag="classifier" if nudge is None else "classifier_retry",
        )

    try:
        raw = _call(None)
    except Exception as e:
        # Упавший ВЫЗОВ не ретраим: это сеть/ключ, а не путаница ролей —
        # повтор чинит редко, а задержку хода удваивает гарантированно.
        log.exception("classifier LLM call failed — деградация, не эскалирую")
        return _degraded(f"вызов LLM упал: {type(e).__name__}: {e}")
    # Условие 1 арки «память»: ответ, упёршийся в max_tokens, — ЯВНАЯ
    # деградация «обрезан», даже если огрызок случайно распарсился бы.
    # Тихий битый JSON уже ловится парсером; тихий ВАЛИДНЫЙ огрызок — нет,
    # поэтому проверка ДО парса. Профиль при обрезке не применяется
    # (degraded=True — вызывающая сторона не пишет профиль).
    # Ретрая здесь тоже нет: повтор упрётся в тот же лимит.
    if getattr(llm, "last_stop_reason", None) == "max_tokens":
        result = _degraded(
            f"ответ обрезан (stop_reason=max_tokens при лимите "
            f"{_CLASSIFIER_MAX_TOKENS}) — поднимите _CLASSIFIER_MAX_TOKENS")
        log.warning("classifier degraded: %s", result.detail)
        return result
    result = parse_classifier_reply(raw)
    # Парс-деградация НЕ логируется внутри parse_classifier_reply — она бы ушла
    # в тишину (инцидент volska 2026-07-22 03:21: classifier_error без следа в
    # логе). Делаем её видимой ЗДЕСЬ: класс сбоя + сырой ответ.
    if not result.degraded:
        return result
    log.warning("classifier degraded: %s | raw=%r — повторяю один раз",
                result.detail, (raw or "")[:120])

    # ЕДИНСТВЕННЫЙ повтор. Дешевле потерянного хода: кэш-префикс цел, платим
    # ~cache-read + выход. Второй повтор не заводим — если модель спутала роль
    # дважды подряд, это уже не флюк, и третья попытка только жжёт секунды,
    # пока лид смотрит на «печатает».
    try:
        raw2 = _call(_RETRY_NUDGE)
    except Exception as e:
        log.exception("classifier retry call failed — деградация")
        return replace(_degraded(f"ретрай упал: {type(e).__name__}: {e}"),
                       retried=True)
    if getattr(llm, "last_stop_reason", None) == "max_tokens":
        return replace(_degraded(
            f"ретрай обрезан (stop_reason=max_tokens при лимите "
            f"{_CLASSIFIER_MAX_TOKENS})"), retried=True)
    retry_result = parse_classifier_reply(raw2)
    if retry_result.degraded:
        log.warning("classifier degraded ПОСЛЕ ретрая: %s | raw=%r",
                    retry_result.detail, (raw2 or "")[:120])
    else:
        log.info("classifier: ретрай спас ход (первый ответ был невалиден)")
    return replace(retry_result, retried=True)


# --- деградация: счётчик + решение об алерте (спека §6, DEV-18) --------------
def note_classifier_error(store, *, now: float, detail: str = "",
                          contact_id: str | None = None) -> None:
    """Считаем КОНЕЧНУЮ деградацию (ретрай не помог) как control-event С КЛАССОМ
    сбоя и С КОНТАКТОМ. Не глотаем в пустоту: пустой detail (старый вызов без
    причины) писать не будем как ''. contact_id нужен форензике: без него из БД
    не видно, чья память замёрзла (сбои 2026-07-23 писались с NULL)."""
    store.add_event("classifier_error", detail=detail or None,
                    contact_id=contact_id, ts=now)


def note_classifier_recovered(store, *, now: float, detail: str = "",
                              contact_id: str | None = None) -> None:
    """Ход, где первый ответ был невалиден, но ретрай спас. Пишем ОТДЕЛЬНЫМ
    видом события: ход не потерян (это не classifier_error), но модель всё
    равно спутала роль — для порога алерта это полноценный сбой."""
    store.add_event("classifier_recovered", detail=detail or None,
                    contact_id=contact_id, ts=now)


def classifier_failure_count(store, *, now: float, window_seconds: float) -> int:
    """Сколько раз за окно классификатор ответил не тем — считая ходы, которые
    спас ретрай. Иначе включение ретрая обнулило бы статистику и порог алерта
    перестал бы срабатывать вообще."""
    since = now - window_seconds
    return (store.count_events("classifier_error", since_ts=since)
            + store.count_events("classifier_recovered", since_ts=since))


def classifier_degraded(store, *, now: float, window_seconds: float, threshold: int) -> bool:
    """True, если сбоев классификатора за окно НАБРАЛОСЬ НА ПОРОГ — тогда
    вызывающая сторона (дебаунсированно) алертит владельца.

    Сравнение `>=`, а не `>` (было до 2026-07-23): со строгим `>` порог 5
    требовал шестого сбоя, и реальные двухсбойные сутки не давали алерта
    никогда — сбои просто копились в БД, которую никто не читает."""
    return classifier_failure_count(
        store, now=now, window_seconds=window_seconds) >= threshold


# --- серия пропущенных обновлений профиля по одному контакту ----------------
# Отдельный сигнал от «сбоев за сутки»: два разных лида по одному сбою — это
# шум, а три подряд по ОДНОМУ лиду — это замёрзшая память в живом диалоге
# (бот выглядит помнящим, но помнит позавчерашнее). Считаем в runtime_flags:
# состояние на контакт, переживает рестарт, отдельной таблицы не заводим.
def _miss_key(contact_id: str) -> str:
    return f"profile_miss:{contact_id}"


def profile_miss_streak(store, contact_id: str) -> int:
    raw = store.get_runtime_flag(_miss_key(contact_id))
    try:
        return int(raw) if raw else 0
    except ValueError:            # руками покорёженный флаг — не роняем ход
        return 0


def note_profile_miss(store, contact_id: str, *, now: float) -> int:
    """Профиль на этом ходу применить НЕ удалось (деградация или перебор
    бюджета). Возвращает новую длину серии."""
    streak = profile_miss_streak(store, contact_id) + 1
    store.set_runtime_flag(_miss_key(contact_id), str(streak), ts=now)
    return streak


def reset_profile_miss(store, contact_id: str, *, now: float = 0.0) -> None:
    """Здоровый ход классификатора — серия обрывается. Даже если нового факта
    не было (profile: null): память не отстаёт, отставать было нечему."""
    store.set_runtime_flag(_miss_key(contact_id), "0", ts=now)
    store.set_runtime_flag(f"profile_miss_alerted:{contact_id}", "0", ts=now)
