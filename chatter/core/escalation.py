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

import logging
import re
import time
from dataclasses import dataclass
from functools import lru_cache

from chatter.core.brand_safety import forbidden_mention
from chatter.core.conversation import next_state
from chatter.core.prompt_log import log_funnel_signal
from chatter.core.disclosure import honest_prefix, is_bot_question
from chatter.core.guardrails import contains_unbacked_claim
from chatter.core.obligations import DEFAULT_PROMISE_TERMS, unbacked_promise
from chatter.core.reply_rules import COMPLAINT_TAG, complaint_promise

logger = logging.getLogger("chatter.escalation")

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


def strip_keyword_section(playbook: str) -> str:
    """Плейбук БЕЗ секции ключевых слов — то, что видит МОДЕЛЬ.

    Спека `2026-08-19-playbook-trim.md` §1(а). Секцию `## Ключові слова
    ескалації` (и её ru/en-варианты) читает КОД: `parse_escalation_keywords`
    строит из неё детерминированный слой эскалации, который срабатывает всегда,
    без модели и без сети. При этом та же секция уезжала в промпт — и в brain,
    и в классификатор, — то есть модель получала список, по которому и так
    отработает код. У volska это 1 083 символа в ДВУХ префиксах, а префикс
    оплачивается записью кэша по двойной ставке.

    ПОЧЕМУ ФУНКЦИЯ ЖИВЁТ ЗДЕСЬ, а не в сборщиках промпта: «где начинается и
    где заканчивается секция ключевых слов» обязано иметь ОДНО определение.
    Вырезалка, написанная рядом с промптом, повторила бы разбор заголовков
    своими словами, и однажды разошлась бы с парсером — вырезала бы не то или
    не всё, а увидели бы мы это по счёту, а не по ошибке. Поэтому обе функции
    стоят рядом и делят `_KEYWORD_HEADINGS`.

    Из ФАЙЛА секция не убирается никогда: она источник для парсера (§4 «всё из
    playbook.md, не хардкод»). Убирается только из текста, уходящего в API.
    """
    lines = (playbook or "").splitlines(keepends=True)
    out: list[str] = []
    in_section = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip().casefold()
            # Тот же переход состояния, что у парсера: заголовок ЗАКАНЧИВАЕТ
            # предыдущую секцию и решает судьбу следующей.
            in_section = heading in _KEYWORD_HEADINGS
            if in_section:
                continue          # саму строку заголовка тоже не отдаём модели
        if in_section:
            continue
        out.append(line)
    return "".join(out)


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
#
# ЗАПАСНОЙ набор: работает всегда, независимо от конфига (у demo/demo2 owner_ref
# не заполнен, и русские/английские роли держатся только им). Второе число —
# БЮДЖЕТ ХВОСТА: сколько букв максимум может дописаться после стема. Он выведен
# из самой длинной НАСТОЯЩЕЙ падежной формы, а не назначен на глаз (DEV-29 §7).
# Без бюджета стем матчился подстрокой, и «керівництво студії ухвалило нові
# ціни» считалось обещанием контакта владельца — замер на живом проде volska
# 13.08. Цена такой ошибки — не только лишняя карточка: на шве H2 (run.py)
# недоставленная карточка + ложное срабатывание = ЗАМЕНА готового ответа
# заглушкой, см. док-строку mentions_owner_contact.
_OWNER_ROLE_STEMS = (
    ("владел", 4),     # владельца +3 · владельцем +4 · владелица +3
    ("хозяин", 2),     # хозяина +1 · хозяином +2
    ("керівниц", 2),   # керівниці +2 · керівницею +2 · «керівництво» +3 — мимо
    ("керівник", 2),   # керівника +1 · керівником +2
    ("власни", 3),     # власник +1 · власника +2 · власником +3
    ("owner", 2),      # owner +0 · owner's +2
)

# Слово = буквы/цифры + апостроф (оба начертания, как в _OWNER_CONTACT_VERBS).
_WORD_RE = re.compile(r"[^\w'’]+")
# Минимальная длина стема роли, выведенного из конфига. Тот же класс защиты, что
# `len(oid) >= 6` для имён ниже: короткий стем матчит пол-словаря («ан» из пары
# «Ані»/«Анею» поймал бы «займатися»).
_MIN_ROLE_STEM = 4


def _words(text: str) -> list[str]:
    return [w for w in _WORD_RE.split((text or "").casefold()) if w]


def _common_prefix(a: str, b: str) -> str:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return a[:n]


def _role_hit(words: "list[str]", stem: str, budget: int) -> bool:
    """Слово НАЧИНАЕТСЯ со стема и хвост не длиннее бюджета.

    Матч по слову, а не подстроке: подстрочный матч бюджет обессмысливает
    («майстерня» содержит «майст» в любом случае) и ловит чужие слова целиком
    («homeowner» ← `owner`)."""
    return any(w.startswith(stem) and len(w) - len(stem) <= budget for w in words)


@lru_cache(maxsize=128)
def _owner_role_spec(owner_id: str, owner_ref: str) -> "tuple[str, int] | None":
    """Стем роли владельца + бюджет хвоста, ВЫВЕДЕННЫЕ ИЗ КОНФИГА клиента
    (DEV-29, схема C). Ничего не угадываем морфологией — обе величины приходят
    из двух падежных форм, которые в конфиге уже есть.

    `owner_id` и `owner_ref` — две формы одного слова по конвенции конфига
    (`owner_ref` — творительный: «зв'яжу вас з {ref}»). Берём ПОСЛЕДНИЕ токены
    пары (носитель роли; определения и служебные слова — «старший», «нашим» —
    отваливаются сами, отдельный стоп-лист не нужен), стем = их общий префикс,
    бюджет = максимальный хвост, который в этих формах реально дописывается.

    Пример yarina: майстер/майстром → («майст», 3). Ловит майстер/майстру/
    майстром, НЕ ловит майстерня (+4) и майстерності (+7) — замер 13.08 на
    репликах ниши, схема «стем из каждой пары» давала там 6 ложных из 7.

    Почему общий префикс, а не отсечение хвоста у одного слова: беглая гласная.
    майстер → майстра/майстру — обрезание на 1–2 символа даёт «майсте», который
    не совпадает НИ С ОДНОЙ косвенной формой.

    None означает «стема из конфига нет, поведение прежнее»: пустой owner_ref
    (demo/demo2), либо роль есть только в одном поле (owner_id = личное имя),
    либо общий префикс короче _MIN_ROLE_STEM. Фолбэка с отсечением хвоста тут
    намеренно НЕТ — он дал бы «майсте» и ложное ощущение работы.
    """
    a_words, b_words = _words(owner_id), _words(owner_ref)
    if not a_words or not b_words:
        return None
    a, b = a_words[-1], b_words[-1]
    stem = _common_prefix(a, b)
    if len(stem) < _MIN_ROLE_STEM:
        return None
    return stem, max(len(a) - len(stem), len(b) - len(stem))


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
_FALLBACK_SELF_ALT = {
    "ru": "Уточню этот момент и вернусь к вам с ответом.",
    "en": "Let me check this and get back to you with an answer.",
    "uk": "Уточню цей момент і повернуся до вас з відповіддю.",
}


def suppressed_fallback(*, language: str = "ru", owner_ref: str | None = None,
                        variant: int = 0) -> str:
    """Что получает ЛИД вместо подавленного ответа: нейтральное «уточню и
    вернусь» + предложение вывести на владельца. Неизвестный язык → ru (тот же
    фолбэк, что console_text/honest_disclosure — лид всегда получает понятный
    текст, а не KeyError).

    ВАЖНО: результат обязан распознаваться mentions_owner_contact БЕЗ подсказки
    owner_ref — иначе недоставленная карточка пропустит обещание контакта
    владельца мимо H2-гейта. Держится глаголом («свяж»/«зв'яж»/«connect») и
    ролевым корнем дефолта; тест это фиксирует."""
    ref = owner_ref or _FALLBACK_OWNER_REF.get(language, _FALLBACK_OWNER_REF["ru"])
    table = _FALLBACK_WITH_OWNER_ALT if variant else _FALLBACK_WITH_OWNER
    return table.get(language, table["ru"]).format(ref=ref)


# P20 (б): вопрос УЖЕ передан владельцу (owner_write delivered — а его закрывает
# КОД по факту доставленной карточки, не модель). Повторять «уточню деталі та
# повернуся» здесь — прямая ложь: мы ничего не уточняем, мы ждём человека.
# Инцидент 2026-07-29: лид спросил «вы уточнили детали?)» и получил в ответ
# «уточню деталі та повернуся» — третью байт-идентичную копию подряд.
# ⚠️ ПАДЕЖ: `owner_ref` в конфигах задан в ТВОРИТЕЛЬНОМ падеже, потому что
# исходный шаблон был «зв'яжу вас з {ref}» (у volska — «керівницею»). Поэтому и
# здесь ref обязан стоять ПОСЛЕ «з/с/with»: «передала питання керівницею» —
# именно это вылезло на офлайн-смоуке. Один ref не может обслужить два падежа.
_FALLBACK_AWAITING_OWNER = {
    "ru": ("Я уже передала ваш вопрос — он сейчас на согласовании с {ref}. "
           "Как только будет ответ, сразу напишу вам."),
    "en": ("I've already passed your question on — it's with {ref} now. "
           "I'll write back as soon as there's an answer."),
    "uk": ("Я вже передала ваше питання — воно зараз на погодженні з {ref}. "
           "Щойно буде відповідь, одразу напишу вам."),
}
# Дефолты — тоже в творительном (см. падежный каветат выше).
_FALLBACK_AWAITING_OWNER_REF = {
    "ru": "владельцем", "en": "the owner", "uk": "керівницею",
}


# Вторые формулировки того же смысла — сырьё для анти-самоповтора (P20 в).
# Вариант обязан нести ТОТ ЖЕ факт: «уточню» нельзя подменять на «передала»,
# пока карточка не доставлена, иначе анти-повтор начнёт врать ради разнообразия.
_FALLBACK_WITH_OWNER_ALT = {
    "ru": "Уточню этот момент и вернусь к вам с ответом. Если удобно, могу связать вас с {ref}.",
    "en": ("Let me check this and get back to you with an answer. If you'd like, "
           "I can connect you with {ref}."),
    "uk": ("Уточню цей момент і повернуся до вас з відповіддю. Якщо зручно, можу "
           "зв'язати вас з {ref}."),
}
_FALLBACK_AWAITING_OWNER_ALT = {
    "ru": "Ваш вопрос уже согласовывается с {ref} — жду ответа и сразу передам вам.",
    "en": ("Your question is already being reviewed with {ref} — I'm waiting for "
           "a reply and will pass it on."),
    "uk": "Ваше питання вже погоджується з {ref} — чекаю на відповідь і одразу передам вам.",
}


def awaiting_owner_fallback(*, language: str = "ru", owner_ref: str | None = None,
                            variant: int = 0) -> str:
    """Что получает лид, когда ответ подавлен, а вопрос УЖЕ у владельца.

    Отличается от `suppressed_fallback` смыслом, а не только словами: там
    «уточню и вернусь» (обещание действия), здесь «передала, ждём» (состояние).
    `variant=1` — вторая формулировка ТОГО ЖЕ факта для анти-самоповтора.
    Неизвестный язык → ru (как и остальные фолбэки — лид получает текст, не
    KeyError)."""
    ref = owner_ref or _FALLBACK_AWAITING_OWNER_REF.get(
        language, _FALLBACK_AWAITING_OWNER_REF["ru"])
    table = _FALLBACK_AWAITING_OWNER_ALT if variant else _FALLBACK_AWAITING_OWNER
    return table.get(language, table["ru"]).format(ref=ref)


def _norm_reply(text: str | None) -> str:
    """Нормализация для сравнения «то же самое сообщение».

    Гасит регистр, разбивку пробелами И косметику `humanizer.humanize_typography`
    (em-dash → дефис, срез «!»). Последнее обязательно: сравнивается СЫРОЙ
    кандидат с УЖЕ отправленным (humanizer — последний шов перед отправкой), и
    без этого заглушка «не совпадала сама с собой» из-за одного тире, а
    анти-самоповтор молча пропускал дубль. Сторож на расхождение с реальным
    humanizer'ом — test_norm_reply_absorbs_humanizer_typography."""
    t = (text or "").replace("—", "-").replace("!", ".")
    return " ".join(t.split()).casefold()


def pick_non_repeating(candidate: str, *, previous: str | None,
                       variants: "tuple[str, ...] | list[str]" = ()) -> str | None:
    """P20 (в): лид не имеет права получить ту же реплику дважды подряд.

    Дедуп карточек владельцу существует с 07-18 (`_ESCALATION_DEDUP_SECONDS`),
    у текста ЛИДУ его не было — и подавление печатало константу сколько угодно
    раз. Возвращает `candidate`, если он отличается от предыдущего исходящего;
    иначе первый непохожий вариант; иначе None — «честная пауза» (промолчать
    ход лучше, чем прислать третью копию; владелец уже уведомлён карточкой)."""
    prev = _norm_reply(previous)
    if not prev or _norm_reply(candidate) != prev:
        return candidate
    for v in variants:
        if _norm_reply(v) != prev:
            return v
    return None


def self_action_fallback(*, language: str = "ru", variant: int = 0) -> str:
    """Заглушка БЕЗ обещания контакта владельца — говорим только то, что персона
    сделает сама. Ставится, когда карточка владельцу не доставлена (H2).
    `variant=1` — вторая формулировка того же для анти-самоповтора (P20 в)."""
    table = _FALLBACK_SELF_ALT if variant else _FALLBACK_SELF
    return table.get(language, table["ru"])


def honest_self_action_fallback(*, language: str = "ru", variant: int = 0) -> str:
    """То же самое, но с честным фактом впереди — версия для honest-режима.

    Условия «а лид точно спрашивал про личность?» здесь НЕТ намеренно. Замер
    volska 2026-07-21 показал, что детектор вопроса (`is_bot_question`) мимо
    даже после расширения: «ти людина?» — самая частая формулировка — не
    ловится, и хвост таких форм бесконечен. Поэтому гарантию держит РЕЖИМ, а не
    распознавание текста: в honest-режиме любая аварийная подмена ответа несёт
    честный факт. Цена — лид, спросивший про цену и попавший на недоставленную
    карточку, увидит лишнюю строку про ассистента; это дёшево по сравнению с
    ответом, из которого следует, что он говорит с человеком."""
    return (f"{honest_prefix(language)} "
            f"{self_action_fallback(language=language, variant=variant)}")


def mentions_owner_contact(reply: str, owner_id: str = "", owner_ref: str | None = None) -> bool:
    """Ответ обещает участие/контакт ЧЕЛОВЕКА-владельца: глагол стороннего
    контакта («свяжется/перезвонит/подключу»), ролевое слово («владелец» в любом
    падеже) ИЛИ имя владельца (в т.ч. склонённое: «Дмитрием»/«Дмитрия»).

    Роль берётся из ДВУХ источников: стем из конфига клиента
    (`_owner_role_spec`, пара owner_id×owner_ref — закрывает КЛАСС «каждый новый
    клиент приносит своё слово роли»: майстер, лікар, тренер) и запасной
    хардкод `_OWNER_ROLE_STEMS`. Оба матчатся по слову с бюджетом хвоста.

    ⚠️ ЦЕНА ЛОЖНОГО СРАБАТЫВАНИЯ РАЗНАЯ НА ДВУХ ШВАХ. Здесь стояло «лучше
    поймать лишнее — лишняя карточка безобидна»; это правда только про первый
    шов, и по второму читатель принимал бы решение по неверной цене:
    - `deterministic_escalation` ниже в этом файле, тег `owner_handoff`:
      suppress=False, ответ уезжает лиду КАК ЕСТЬ, владелец получает лишнюю
      карточку. Цена — шум у владельца, действительно дёшево.
    - гейт H2 в `chatter/run.py` (ветка `not protected and not delivered and
      implies_owner`): `reply` ЗАМЕНЯЕТСЯ заглушкой само-действия. Ложное
      срабатывание тут СЪЕДАЕТ готовый ответ — «наша майстерня працює з 9:00
      до 18:00» уйдёт лиду как «уточню і повернуся до вас». Съедает не на
      каждом ходу, а когда карточка не доставлена (нет notifier, сбой notify,
      тихая правка карточки внутри окна дедупа) — но ровно ради этого пути
      гейт и написан. На доставленном пути расплата меньше и не нулевая:
      `_drop_trailing_question` срезает у ответа хвостовой вопрос.

    Отсюда: расширять предикат «на всякий случай» НЕЛЬЗЯ. Замер 13.08 на роли
    «старший майстер»: вывод стема из каждой пары форм дал бы 6 ложных на 7
    безобидных репликах ниши («старших автомобілів», «майстерня», «майстерності»)
    — это 6 ответов под подмену, а не 6 лишних карточек. Тот же вывод уже сделан
    независимо в `core/guardrails.py`: формула редакции намеренно не упоминает
    владельца, иначе H2 затирает аккуратную редакцию обратно в заглушку.

    Имя матчим по стему (owner_id без последней буквы) ТОЛЬКО для длинных имён
    (≥6 симв.), чтобы короткие имена не давали ложных подстрок («Аня» →
    «заняться»)."""
    low = (reply or "").casefold()
    if any(v in low for v in _OWNER_CONTACT_VERBS):
        return True
    words = _words(reply)
    if any(_role_hit(words, stem, budget) for stem, budget in _OWNER_ROLE_STEMS):
        return True
    spec = _owner_role_spec(owner_id or "", owner_ref or "")
    if spec is not None and _role_hit(words, *spec):
        return True
    oid = (owner_id or "").casefold()
    ref = (owner_ref or "").casefold()
    if (oid and oid in low) or (ref and ref in low):
        return True
    # Грубый стем ИМЕНИ (обрезание хвоста, матч подстрокой) — только когда пары
    # форм НЕТ. Если пара есть, стем уже выведен из неё выше, с бюджетом; грубая
    # ветка тогда не добавляет покрытия, а только ложные срабатывания. Замер
    # volska 13.08: owner_id == "Керівниця" → oid[:-1] == "керівниц" → «керівницТВО
    # студії ухвалило нові ціни» считалось обещанием контакта владельца (и на шве
    # H2 съедало бы ответ). Ограничение ≥6 символов оставлено как было: короткие
    # имена дают ложные подстроки («Аня» → «заняться»).
    if spec is None:
        if oid and len(oid) >= 6 and oid[:-1] in low:
            return True
        if ref and len(ref) >= 6 and ref[:-2] in low:
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
    strict_knowledge: bool = True, lead_numbers: frozenset[str] = frozenset(),
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
    # Правило претензии (спека 17.08, решение 7). Стоит ДО обещаний вне базы
    # намеренно: «зробимо безкоштовно» ловится и старым слоем, но там оно
    # называется «обещание вне базы», а владельцу нужно другое слово — бот
    # принял решение о компенсации ЗА него. Разные строки «почему» в карточке
    # требуют от человека разных действий.
    #
    # Проверяется ОТВЕТ, а не входящее: лид имеет право требовать возврат
    # этими же словами — это работа слоя ключевых слов, и подавлять там нечего.
    complaint = complaint_promise(reply or "")
    if complaint:
        return EscalationReason(
            tag=COMPLAINT_TAG,
            detail=f"ответ решил вопрос компенсации за владельца «{complaint}»",
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
    if contains_unbacked_claim(reply or "", knowledge or "",
                               lead_numbers=lead_numbers):
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


def advance_funnel(store, contact_id: str, *, stage_signal: str | None, escalated: bool,
                   now: float | None = None, bought: bool = False) -> str:
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
    if bought:
        # Оплата — ФАКТ от владельца, а не догадка классификатора, поэтому она
        # закрывает воронку из ЛЮБОГО состояния. Таблица переходов такого ребра
        # не знает («new → closed» её нет), и это правильно для сигналов модели,
        # но неверно для решения человека — как и эскалация, это оверрайд.
        new = "closed"
    elif escalated:
        new = "escalated"
    elif stage_signal:
        new = next_state(current, stage_signal)
    else:
        new = current
    # Лог — ДО записи и БЕЗ условия `new != current`: холостой ход в БД не
    # попадает по дизайну, поэтому лог остаётся единственным следом сигнала,
    # который воронка проглотила (дыра `new + interested`, закрыта 2026-08-09).
    try:
        log_funnel_signal(contact_id=contact_id,
                          signal="bought" if bought else stage_signal,
                          from_state=current, to_state=new, escalated=escalated)
    except Exception:              # DEV-18: наблюдаемость не роняет ход лиду
        logger.exception("не удалось залогировать сигнал воронки для %s", contact_id)

    if new != current:
        store.set_state(contact_id, new)
        # Фундамент дашборда (CLIENT_SCREENS §5.2): `set_state` ПЕРЕЗАПИСЫВАЕТ
        # поле, не оставляя ни ts, ни прошлого значения. Пишем переход здесь —
        # только здесь известны оба конца и сигнал. Холостой ход не пишем: он
        # раздул бы метрику «квалифицировано» на пустом месте.
        # Отсутствие метода (старый Store в чужом тесте) не имеет права ронять
        # живой ход — воронка это аналитика, а не доставка ответа лиду.
        rec = getattr(store, "record_transition", None)
        if rec is not None:
            try:
                rec(contact_id, from_state=current, to_state=new,
                    signal=("bought" if bought else
                            "escalated" if escalated else stage_signal),
                    ts=time.time() if now is None else now)
            except Exception:              # DEV-18: пишем громко, но не падаем
                logger.exception("не удалось записать переход воронки для %s", contact_id)
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
