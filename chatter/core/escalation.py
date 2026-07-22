"""Эскалация: детерминированный слой + оживление воронки (арка 3B).

ЧИСТЫЙ модуль — ноль сети, ноль LLM, ноль Telethon. Классификатор (LLM) живёт
отдельно в classifier.py. Здесь только бесплатные детерминированные триггеры,
которые работают, даже если классификатор/сеть лежат (спека §4, слой 1), и
тонкая обёртка над мёртвым `conversation.next_state` (§5).

ШОВ (§8): этот модуль ВЫЗЫВАЕТ `disclosure.is_bot_question`,
`guardrails.contains_unbacked_claim` и `conversation.next_state`, но НЕ правит
ни один из пяти core-файлов.
"""
from __future__ import annotations

from dataclasses import dataclass

from chatter.core.brand_safety import forbidden_mention
from chatter.core.conversation import next_state
from chatter.core.disclosure import honest_prefix, is_bot_question
from chatter.core.guardrails import contains_unbacked_claim
from chatter.core.obligations import DEFAULT_PROMISE_TERMS, unbacked_promise

# Зеркалит conversation._TERMINAL (приватное там). Завершённый диалог не
# воскрешаем ни сигналом воронки, ни эскалацией.
_TERMINAL_STATES = frozenset({"closed", "dead"})

# Заголовки секции ключевых слов по языкам (settings.language). Значение
# заголовка не важно для парсинга по существу — важно найти начало списка.
_KEYWORD_HEADINGS = (
    "ключевые слова эскалации",   # ru
    "escalation keywords",        # en
    "ключові слова ескалації",    # uk
)


def parse_escalation_keywords(playbook: str) -> list[str]:
    """Достаёт детерминированные ключевые слова из playbook.md (§4: «Всё из
    playbook.md, не хардкод»).

    Конвенция: секция `## <один из _KEYWORD_HEADINGS>`, дальше пункты списка
    `- слово`, до следующего заголовка (`#`). Отсутствие секции → `[]` (слой
    ключевых слов просто выключен, это не ошибка). Слова casefold'ятся, чтобы
    матч был регистронезависимым.
    """
    lines = (playbook or "").splitlines()
    out: list[str] = []
    in_section = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip().casefold()
            in_section = heading in _KEYWORD_HEADINGS
            continue
        if not in_section:
            continue
        if stripped.startswith("- "):
            word = stripped[2:].strip()
            if word:
                out.append(word.casefold())
    return out


def esc_active_key(contact_id: str) -> str:
    """runtime_flag с ref открытой (не закрытой владельцем) карточки эскалации
    контакта — движок дедупа (Fix 2): пока флаг непуст, повторная эскалация
    правит ту же карточку; тап владельца (route_callback) его чистит →
    следующая эскалация создаёт новую карточку. Общий ключ для run.py и
    control_bot.py, чтобы обе стороны смотрели в одно место."""
    return f"esc_active:{contact_id}"


# Глаголы стороннего контакта + ролевые стемы владельца. Держатся ЗДЕСЬ (не в
# run.py), потому что их использует и триггер owner_handoff, и H2-детектор в
# run.py — один источник правды, иначе разъедутся (как разъехались keyword-слой
# и H2 на дриле 07-19).
# uk «зв'яжу» — в двух вариантах апострофа: мы пишем U+0027, но модель нередко
# отдаёт типографский U+2019, и один вариант молча пропускал бы обещание.
_OWNER_CONTACT_VERBS = (
    "свяж", "перезвон", "передзвон", "созвон", "подключ",
    "зв'яж", "зв’яж", "connect you with",
)
# ru: владелец/владельцем/владельца/…; uk: керівниця/керівниці/керівницею и
# керівник/керівника/керівником. Украинские корни держим ОТДЕЛЬНО от owner_id:
# у volska owner_id == "Керівниця", и ветка матча по стему ИМЕНИ закрывала роль
# совпадением — смена owner_id на реальное имя молча вернула бы дыру, и
# «передам керівниці» уехало бы лиду БЕЗ карточки владельцу.
_OWNER_ROLE_STEMS = ("владел", "хозяин", "керівниц", "керівник", "власни", "owner")


# Заглушки, которые получает ЛИД вместо подавленного ответа. Локализованы по
# settings.language ровно как honest_disclosure: аварийная фраза на чужом языке
# — это тот же провал доверия, что и честность на чужом языке (баг живого теста
# volska 2026-07-21: украиноязычный лид получил русское «уточню детали ... с
# владельцем»). Дефолт роли владельца тоже per-language, иначе украинский текст
# заканчивался бы русским словом.
_FALLBACK_OWNER_REF = {"ru": "владельцем", "en": "the owner", "uk": "власником"}
_FALLBACK_WITH_OWNER = {
    "ru": "Хороший вопрос — уточню детали и вернусь. Если удобно, свяжу вас с {ref}.",
    "en": ("Good question — let me check the details and come back to you. "
           "If you'd like, I can connect you with {ref}."),
    "uk": "Гарне питання — уточню деталі та повернуся. Якщо зручно, зв'яжу вас з {ref}.",
}
_FALLBACK_SELF = {
    "ru": "Хороший вопрос — уточню детали и вернусь к вам.",
    "en": "Good question — let me check the details and come back to you.",
    "uk": "Гарне питання — уточню деталі та повернуся до вас.",
}


def suppressed_fallback(*, language: str = "ru", owner_ref: str | None = None) -> str:
    """Что получает ЛИД вместо подавленного ответа: нейтральное «уточню и
    вернусь» + предложение вывести на владельца. Неизвестный язык → ru (тот же
    фолбэк, что console_text/honest_disclosure — лид всегда получает понятный
    текст, а не KeyError).

    ВАЖНО: результат обязан распознаваться mentions_owner_contact БЕЗ подсказки
    owner_ref — иначе недоставленная карточка пропустит обещание контакта
    владельца мимо H2-гейта. Держится глаголом («свяж»/«зв'яж»/«connect») и
    ролевым корнем дефолта; тест это фиксирует."""
    ref = owner_ref or _FALLBACK_OWNER_REF.get(language, _FALLBACK_OWNER_REF["ru"])
    template = _FALLBACK_WITH_OWNER.get(language, _FALLBACK_WITH_OWNER["ru"])
    return template.format(ref=ref)


def self_action_fallback(*, language: str = "ru") -> str:
    """Заглушка БЕЗ обещания контакта владельца — говорим только то, что персона
    сделает сама. Ставится, когда карточка владельцу не доставлена (H2)."""
    return _FALLBACK_SELF.get(language, _FALLBACK_SELF["ru"])


def honest_self_action_fallback(*, language: str = "ru") -> str:
    """То же самое, но с честным фактом впереди — версия для honest-режима.

    Условия «а лид точно спрашивал про личность?» здесь НЕТ намеренно. Замер
    volska 2026-07-21 показал, что детектор вопроса (`is_bot_question`) мимо
    даже после расширения: «ти людина?» — самая частая формулировка — не
    ловится, и хвост таких форм бесконечен. Поэтому гарантию держит РЕЖИМ, а не
    распознавание текста: в honest-режиме любая аварийная подмена ответа несёт
    честный факт. Цена — лид, спросивший про цену и попавший на недоставленную
    карточку, увидит лишнюю строку про ассистента; это дёшево по сравнению с
    ответом, из которого следует, что он говорит с человеком."""
    return f"{honest_prefix(language)} {self_action_fallback(language=language)}"


def mentions_owner_contact(reply: str, owner_id: str = "", owner_ref: str | None = None) -> bool:
    """Ответ обещает участие/контакт ЧЕЛОВЕКА-владельца: глагол стороннего
    контакта («свяжется/перезвонит/подключу»), ролевое слово («владелец» в любом
    падеже) ИЛИ имя владельца (в т.ч. склонённое: «Дмитрием»/«Дмитрия»).

    Философия сети H1: лучше поймать лишнее (безобидная лишняя карточка), чем
    пропустить обещание контакта. Имя матчим по стему (owner_id без последней
    буквы) ТОЛЬКО для длинных имён (≥6 симв.), чтобы короткие имена не давали
    ложных подстрок («Аня» → «заняться»)."""
    low = (reply or "").casefold()
    if any(v in low for v in _OWNER_CONTACT_VERBS):
        return True
    if any(s in low for s in _OWNER_ROLE_STEMS):
        return True
    oid = (owner_id or "").casefold()
    if oid and (oid in low or (len(oid) >= 6 and oid[:-1] in low)):
        return True
    ref = (owner_ref or "").casefold()
    if ref and (ref in low or (len(ref) >= 6 and ref[:-2] in low)):
        return True
    return False


@dataclass(frozen=True)
class EscalationReason:
    """Почему диалог эскалирован — для строки «почему» в карточке (§3).

    `suppress` — надо ли ЗАМЕНИТЬ ответ Ани безопасным, или он едет лиду как
    есть. Раньше это выводилось из `tag` списком в run.py; теперь решение
    принимается ЗДЕСЬ, потому что оно зависит не только от тега, но и от
    per-client тумблера strict_knowledge (одно и то же обещание подавляется в
    строгом режиме и не подавляется в свободном). Держать полутон «эскалируем,
    но не подавляем» в вызывающем коде значило бы разложить одно решение по
    двум файлам.
    """
    tag: str        # "keyword" | "bot_question" | "unbacked_claim" | "owner_handoff" | "classifier"
    detail: str     # человеческая однострочная причина
    suppress: bool = False


def deterministic_escalation(
    *, incoming_text: str, reply: str, knowledge: str, keywords: list[str],
    forbidden_terms=(), promise_terms=DEFAULT_PROMISE_TERMS,
    owner_id: str = "", owner_ref: str | None = None,
    strict_knowledge: bool = True,
) -> EscalationReason | None:
    """Слой 1 (спека §4): бесплатные детерминированные триггеры. Работают, даже
    если классификатор/сеть лежат. Возвращает ПЕРВЫЙ сработавший триггер, иначе
    None.

    Порядок по КРИТИЧНОСТИ: brand-safety в ответе (запрещённое — рубли/росбанк) →
    необеспеченное ОБЕЩАНИЕ в ответе (скидка/гарантия/«свяжется» вне базы, H1) →
    ключевое слово → вопрос про бота → brand-safety во входящем → необеспеченная
    ЦИФРА в ответе. Оба «в ответе»-триггера (forbidden_reply, unbacked_promise)
    идут ДО keyword: иначе keyword эскалирует, но run.py НЕ подавит ответ, и
    запрещённое/обещание уйдёт лиду.

    ШОВ: вызывает is_bot_question/contains_unbacked_claim/forbidden_mention/
    unbacked_promise, не правит core-файлы.
    """
    hit = forbidden_mention(reply or "", forbidden_terms)
    if hit:
        return EscalationReason(
            tag="forbidden_reply", detail=f"ответ упомянул запрещённое «{hit}»",
            suppress=True)
    promise = unbacked_promise(reply or "", knowledge or "", promise_terms)
    # strict_knowledge=True (дефолт): обещание вне базы подавляется ЗДЕСЬ,
    # раньше остальных — как и было.
    if promise and strict_knowledge:
        return EscalationReason(
            tag="unbacked_promise", detail=f"обещание вне базы знаний «{promise}»",
            suppress=True)
    # strict_knowledge=False: обещание больше не подавляется, но карточка
    # владельцу остаётся. КРИТИЧНО — его нельзя вернуть здесь же с suppress=False:
    # он проверяется РАНЬШЕ unbacked_claim и затенил бы его, а «сделаю скидку
    # 700 грн» обязано подавиться выдуманной ЦИФРОЙ (цифры и brand-safety держим
    # жёстко в обоих режимах). Поэтому неподавляющее обещание откладывается в
    # хвост цепочки и возвращается, только если не сработал никто «сильнее».
    soft_promise = (
        EscalationReason(
            tag="unbacked_promise",
            detail=f"обещание вне базы знаний «{promise}» (свободный режим: не подавлено)",
            suppress=False)
        if promise else None)
    text = (incoming_text or "").casefold()
    for kw in keywords:
        if kw and kw in text:
            return EscalationReason(tag="keyword", detail=f"ключевое слово «{kw}»")
    if is_bot_question(incoming_text or ""):
        return EscalationReason(tag="bot_question", detail="спросили, бот ли это")
    in_hit = forbidden_mention(incoming_text or "", forbidden_terms)
    if in_hit:
        return EscalationReason(
            tag="forbidden_incoming", detail=f"лид упомянул запрещённое «{in_hit}»")
    if contains_unbacked_claim(reply or "", knowledge or ""):
        return EscalationReason(
            tag="unbacked_claim", detail="ответ обещал цену/срок вне базы знаний",
            suppress=True)
    if soft_promise is not None:
        return soft_promise
    # owner_handoff — ПОСЛЕДНИЙ и НЕ suppress: ответ передаёт лида владельцу
    # («обсудить с владельцем», «Дмитрий свяжется», склонённое имя). Идёт после
    # всех suppress-триггеров (иначе выдуманная цена/скидка+«обсудим с владельцем»
    # ушла бы неподавленной). Гарантирует карточку владельцу ДЕТЕРМИНИРОВАННО,
    # без опоры на опциональный классификатор (дрил 07-19: скидочный хэндофф
    # молча уходил мимо владельца). Ответ НЕ подавляем — честный отказ сохраняем.
    if mentions_owner_contact(reply or "", owner_id=owner_id, owner_ref=owner_ref):
        return EscalationReason(
            tag="owner_handoff", detail="ответ предлагает контакт/участие владельца")
    return None


def advance_funnel(store, contact_id: str, *, stage_signal: str | None, escalated: bool) -> str:
    """Оживляет мёртвый `conversation.next_state` (§5): stage_signal
    классификатора гонит воронку new→qualifying→hot→escalated. Эскалация —
    внешний оверрайд (сильнее переходов воронки): уводит в 'escalated' сразу,
    даже если из текущего состояния такого перехода по сигналу нет.

    Завершённый диалог (closed/dead) не трогаем. Пишем в БД только при реальной
    смене состояния. ШОВ: вызывает `next_state`, не правит conversation.py.
    """
    current = store.get_or_create_contact(contact_id)["state"]
    if current in _TERMINAL_STATES:
        return current
    if escalated:
        new = "escalated"
    elif stage_signal:
        new = next_state(current, stage_signal)
    else:
        new = current
    if new != current:
        store.set_state(contact_id, new)
    return new


@dataclass(frozen=True)
class EscalationDecision:
    escalate: bool
    reason: str            # человеческое «почему» для карточки
    stage_signal: str | None
    degraded: bool


def decide_escalation(*, det: "EscalationReason | None", classifier_result) -> EscalationDecision:
    """Свести два слоя (§4). Детерминированный слой достоверен, поэтому его
    причина приоритетнее reason классификатора. Классификатор эскалирует ТОЛЬКО
    если он не деградировал (§6: мусорный/упавший классификатор не эскалирует).
    stage_signal берём у классификатора (даже при не-эскалации — воронку гонит
    сигнал стадии). `classifier_result` — ClassifierResult или None."""
    escalate = det is not None
    reason = det.detail if det is not None else ""
    stage_signal = None
    degraded = False
    if classifier_result is not None:
        degraded = classifier_result.degraded
        if not degraded:
            stage_signal = classifier_result.stage_signal
            if classifier_result.escalate:
                escalate = True
                if not reason:
                    reason = classifier_result.reason or "классификатор: горячий лид"
    return EscalationDecision(
        escalate=escalate, reason=reason, stage_signal=stage_signal, degraded=degraded)
