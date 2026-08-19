"""Keep-alive prompt-кэша: чем, что и когда пинговать (спека 2026-08-09).

Ход после разрыва ≥ TTL платит ЗАПИСЬ по ставке 1h ($6/M) вместо ЧТЕНИЯ
($0.30/M) — отношение ровно 20× (замер Г2, 10.08). Живой трафик даёт 24.1%
таких ходов (замер Г0), и все они до одного — истёкший TTL, а не поломка
префикса. Значит дешёвое касание раз в 50 минут адресует 100% текущих промахов.

Модуль знает ТРИ вещи и больше ничего: как выглядит пинг (§2), когда он нужен
(§3) и что делать с его промахом (§5) — включая доставку алерта владельцу,
потому что промах, ушедший в лог, равносилен промаху незамеченному. Расписание
и тумблер клиента живут снаружи — здесь ни планировщика, ни глобального
состояния.

ГЛАВНОЕ ПРАВИЛО МОДУЛЯ: префикс собирают ТЕ ЖЕ функции, что и боевой путь.
Своя сборка совпала бы с боевой ровно до первой правки промпта, а дальше пинг
грел бы ЧУЖУЮ кэш-запись: деньги уходят, кэш остаётся холодным, и снаружи это
неотличимо от работающей арки (§6 риск 1). Единственный видимый след такого
дефекта — `cache_read == 0` в ответе на пинг, поэтому он и попадает в
`PingResult`.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime

from chatter.core.brain import KYIV_TZ, build_system_prompt
from chatter.core.cache_health import CACHE_TTL_SEC, KEEPALIVE_TAG_PREFIX
from chatter.core.classifier import classifier_stable_prefix
from chatter.core.console import console_text, escape_html
from chatter.core.llm import cached_system_blocks
from chatter.notify.base import Card

log = logging.getLogger("chatter.core.keepalive")

# Период пинга (§3). TTL записи — час, продлевается использованием; 50 минут
# оставляют 10 минут запаса на дрожание планировщика, сетевой ретрай и часовой
# сдвиг. Меньше 50 — линейный рост стоимости без выигрыша; больше 55 — риск
# проскочить TTL, а проскок стоит в 20 раз дороже сэкономленного.
PING_PERIOD_SEC: int = 3000

# Боевой тег → тег его пингов. Значения НЕ переписаны литералами: имя строит
# `KEEPALIVE_TAG_PREFIX` из `cache_health`, где живёт и читатель этих строк.
# Два одинаковых литерала в разных модулях расходятся молча — и расходятся
# ровно тогда, когда кто-то переименует один из них (§5 п.3).
BRAIN_TAG = "brain"
CLASSIFIER_TAG = "classifier"
TAG_MAP: dict[str, str] = {t: KEEPALIVE_TAG_PREFIX + t
                           for t in (BRAIN_TAG, CLASSIFIER_TAG)}

# Механизм §2: запрос выполняет prefill (то есть КАСАЕТСЯ кэш-записи и
# продлевает TTL) и возвращается сразу — `content: []`, `stop_reason:
# max_tokens`, выходные токены не тарифицируются.
PING_MAX_TOKENS = 0

# Минимальная заглушка `messages`. Пустой массив и пустой content API не
# принимает, а содержимое роли не играет: breakpoint стоит на system-блоке,
# messages-tier в кэшируемый префикс не входит.
PING_USER_TEXT = "."

# Окно замера объёма для порога включения (§10 п.2). Порог назван в «диалогах
# в месяц», поэтому и окно — месяц; скользящее, а не календарное: календарное
# первого числа обнуляло бы объём и выключало режим у живого клиента.
VOLUME_WINDOW_SEC: float = 30 * 24 * 3600.0

# Ключ дебаунса алерта о сломанном префиксе. В нём И слаг, И тег: один Store
# делится между персонами процесса, а сломанный brain у одного клиента и
# сломанный классификатор у другого — два разных дефекта. Общий ключ погасил бы
# второй алерт первым, и владелец узнал бы ровно про одну из двух поломок.
ALERT_FLAG_PREFIX = "keepalive_prefix_broken_alerted"


def alert_flag_key(slug: str, tag: str) -> str:
    return f"{ALERT_FLAG_PREFIX}:{slug}:{tag}"


# Какие БОЕВЫЕ теги трогают кэш-запись этого префикса. Не то же самое, что
# «какие теги мы пингуем»: `classifier_retry` шлёт ТОТ ЖЕ стабильный префикс
# (нудж едет после breakpoint'а), поэтому отдельной записи не создаёт (§1), но
# TTL продлевает — и, значит, делает пинг ненужным ровно как обычный вызов.
#
# ⚠️ Собственных пингов здесь НЕТ, и это не забывчивость. `llm_usage` не знает
# КЛИЕНТА: у строки есть тег, но нет слага, а `primary_store()` отдаёт ОДИН
# store всем персонам процесса. Свои касания мы поэтому помним пер-клиентно, в
# `runtime_flags` (`ping_flag_key`) — схема не меняется, а «кто именно грел»
# перестаёт быть догадкой.
#
# Это закрывает ТОЛЬКО нашу половину вопроса: чужие, БОЕВЫЕ вызовы в общей БД
# различить по-прежнему нечем, и потому процесс с двумя персонами и включённым
# тумблером отказывается громко (§6.1; отказ живёт в
# `telethon_run.keepalive_tick`). Настоящая починка — колонка клиента в
# `llm_usage` — вынесена ЗА арку сознательно: это денежный леджер, по которому
# посчитаны ВСЕ числа спеки, и менять линейку в том же мерже, где по ней
# меряют эффект, нельзя.
_TOUCH_TAGS: dict[str, tuple[str, ...]] = {
    BRAIN_TAG: (BRAIN_TAG,),
    CLASSIFIER_TAG: (CLASSIFIER_TAG, "classifier_retry"),
}

# Ключ пер-клиентной памяти о СВОЁМ последнем пинге.
PING_FLAG_PREFIX = "keepalive_last_ping"


def ping_flag_key(slug: str, tag: str) -> str:
    """Ключ `runtime_flags` с моментом последнего НАШЕГО пинга этого префикса
    этого клиента. Переживает рестарт раннера (в отличие от памяти процесса) и
    не путает клиентов (в отличие от тега в `llm_usage`)."""
    return f"{PING_FLAG_PREFIX}:{slug}:{tag}"


@dataclass(frozen=True)
class PingResult:
    """Исход одного пинга. `tag` — БОЕВОЙ тег префикса, а не тег строки в
    `llm_usage`: наружу отвечаем на вопрос «какой префикс грели»."""
    tag: str
    sent: bool
    # Прочитано из кэша в ответе НА ПИНГ. 0 при sent=True — промах, то есть
    # либо пинг опоздал, либо (хуже) греется не тот префикс; §5 различает эти
    # два случая по времени предыдущего касания.
    cache_read: int
    error: str | None


def in_window(now: float, window: tuple[int, int] | None) -> bool:
    """Попадает ли момент `now` в окно суток клиента.

    Окно — параметр клиента, не константа (§3, решение владельца 19.08): лиды
    пишут не круглосуточно, а 24/7 против 13ч/сут это почти двукратная разница
    в стоимости режима. `None` = ограничения нет.

    Время местное (Europe/Kyiv, та же зона, что у часов персоны в `brain`):
    окно называет владелец в часах СВОИХ лидов, а не в UTC.

    Начало позже конца — ночная смена через полночь, а не ошибка: у клиента с
    ночными лидами окно именно такое.
    """
    if window is None:
        return True
    start, end = window
    minute = _minute_of_day(now)
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


def _minute_of_day(now: float) -> int:
    dt = datetime.fromtimestamp(now, KYIV_TZ)
    return dt.hour * 60 + dt.minute


def due_tags(*, now: float, last_call_ts: dict[str, float | None],
             period_sec: int = PING_PERIOD_SEC,
             window: tuple[int, int] | None = None) -> tuple[str, ...]:
    """Какие боевые теги пора пинговать ПРЯМО СЕЙЧАС.

    `last_call_ts`: боевой тег → момент последнего КАСАНИЯ его кэш-записи
    (боевой вызов, его ретрай или наш же прошлый пинг — всё это продлевает
    TTL одинаково, см. `_TOUCH_TAGS`).

    Правило §3: «пинг не нужен, когда трафик и так частый». Живой ход сам
    продлевает запись, поэтому пингуем, только если с последнего касания
    прошло БОЛЬШЕ периода. Это убирает пинги в рабочие часы активного клиента
    — а именно там их было бы больше всего.

    Тег БЕЗ единого вызова в истории (`None`) НЕ пингуется. Кэш-записи ещё не
    существует, и пинг не поддержал бы её, а СОЗДАЛ по ставке записи. Прогрев
    на старте — соседняя задача с другим триггером и отдельным замером, и
    спека выносит её за границу арки прямым текстом (§7). Первый боевой вызов
    сам создаст запись, и с этого момента мы её держим.
    """
    out = []
    if not in_window(now, window):
        return ()
    for tag in TAG_MAP:
        ts = last_call_ts.get(tag)
        if ts is None:
            continue
        if now - ts > period_sec:
            out.append(tag)
    return tuple(out)


def build_ping_payload(cfg, tag: str) -> dict:
    """kwargs боевого запроса ДО брейкпоинта — то есть форма пинга (§2).

    Копия боевого запроса, а не «похожий запрос»: та же модель, тот же
    `system[0]` байт-в-байт вместе с `cache_control ttl:1h`, `thinking` как в
    бою. Расхождение в любом из трёх делает это ДРУГИМ префиксом, и пинг
    греет чужую запись.

    `system[1]` (изменчивый хвост — профиль, обязательства, время) НЕ шлётся:
    он живёт ПОСЛЕ breakpoint'а и в кэшируемый префикс не входит. Отправить
    его значило бы платить за токены, которые кэш всё равно не хранит.

    Импорты чужих модулей ленивые по той же причине, что в `prefix_budget`:
    `chatter.run` тянет пол-продукта, а этот модуль обязан импортироваться в
    офлайне и не замыкать кольцо с раннером.
    """
    from chatter.core.prefix_budget import classifier_model_of
    from chatter.run import obligations_slot_enabled

    if tag == BRAIN_TAG:
        system = build_system_prompt(cfg)
        model = cfg.settings.model
        # thinking НЕ передаём вовсе — в бою brain его тоже не передаёт и
        # получает адаптивный дефолт модели. Прислать сюда `disabled` значило
        # бы отправить другой запрос, чем боевой. Совместимость связки
        # `max_tokens: 0` + адаптивный thinking проверена живьём (гейт Г1).
        thinking: dict = {}
    elif tag == CLASSIFIER_TAG:
        # Форма префикса классификатора ЗАВИСИТ от слота обязательств
        # (`track_obligations` добавляет инструкции о нём, ~900 токенов
        # разницы на боевых клиентах). Флаг читается из ЕДИНСТВЕННОГО
        # источника — того же, что у сторожа порога кэша; свой `os.getenv`
        # здесь стал бы вторым числом на одну вещь и разошёлся бы с раннером
        # молча, а пинг грел бы префикс той формы, которой в API не уходит.
        system = classifier_stable_prefix(
            cfg.playbook, cfg.settings.language,
            cfg.settings.limits.profile_budget_tokens,
            track_obligations=obligations_slot_enabled())
        # Не `cfg.settings.model`: вопрос «какой моделью считает
        # классификатор» имеет ОДИН ответ на весь код, и он живёт в
        # prefix_budget. Сегодня это та же модель; в день, когда появится
        # отдельное поле, пинг обязан поехать за ней без правки здесь.
        model = classifier_model_of(cfg)
        # В бою классификатор ходит с no_thinking=True (иначе sonnet-5 сжигает
        # весь бюджет на невидимое мышление). `disabled` не входит в список
        # запретов для max_tokens: 0 (§2).
        thinking = {"thinking": {"type": "disabled"}}
    else:
        raise ValueError(
            f"keep-alive: нечего пинговать под тегом {tag!r} — известны "
            f"{sorted(TAG_MAP)}. Кэшируемых префиксов ровно два на клиента "
            f"(§1), и новый не появляется молча")

    return {"model": model,
            "max_tokens": PING_MAX_TOKENS,
            "system": cached_system_blocks(system),
            "messages": [{"role": "user", "content": PING_USER_TEXT}],
            **thinking}


def _classifier_prefix_is_cacheable() -> bool:
    """Раскладка классификатора под кэш включена (флаг CHATTER_CLASSIFIER_CACHE)?

    При выключенном флаге системный промпт классификатора собирается ВМЕСТЕ с
    профилем лида, то есть меняется на каждом ходу. Пинговать в этом режиме
    нечего: `classifier_stable_prefix` вернул бы текст, которого в API не
    уходит НИ ОДИН боевой вызов, и мы бы честно платили за прогрев записи,
    которую никто никогда не прочитает. Гардиан в проде флаг ставит; здесь
    важно, что при выключенном мы молчим, а не жжём деньги.

    Читаем ту же функцию, что и сам классификатор: второй `os.getenv` рядом
    разошёлся бы с ней ровно в момент правки имени флага.
    """
    from chatter.core.classifier import _cache_enabled
    return _cache_enabled()


async def run_keepalive_cycle(cfg, store, llm, *, now: float | None = None,
                              notifier=None) -> list[PingResult]:
    """Один проход по префиксам ОДНОГО клиента. НИКОГДА не бросает наружу.

    Порядок проверок — от самой дешёвой к самой дорогой, и это не стиль:
    тумблер и окно решаются без единого запроса, объём — одним, и только после
    них мы вообще смотрим в историю вызовов.

    Три жёстких правила §5 держатся здесь:
      1. Сбой пинга не трогает лида — исключение ловится ПОСТРОЧНО, по тегу, и
         превращается в `PingResult(sent=False, error=...)` плюс громкий лог.
      2. Сбой пинга НЕ считается сбоем классификатора: мы не пишем ни одного
         `control_event`, который читает порог `classifier_error_threshold`
         (=2/сутки). Иначе сетевая икота ФОНОВОЙ задачи поднимала бы боевой
         алерт деградации классификатора и обнуляла смысл порога.
      3. Строка в `llm_usage` пишется под тегом `keepalive_*` (`TAG_MAP`).
         Под боевым тегом пинг изнутри неотличим от боевого вызова и мгновенно
         портит и hit-rate, и замер экономии §4.

    Ретрая внутри прохода нет НАМЕРЕННО: сон на 60 с ради одного клиента
    задержал бы проход остальных, а §3 отводит на «дрожание планировщика,
    сетевой ретрай и часовой сдвиг» общий запас в 10 минут — следующий тик
    фонового цикла укладывается в него с большим зазором.
    """
    ka = cfg.settings.keepalive
    if not ka.enabled:
        # Тумблер выключен → ни одного запроса, ни одной строки: поведение
        # клиента, который про арку не знает, обязано быть прежним байт-в-байт.
        return []
    now = time.time() if now is None else now
    window = ka.window_minutes
    if not in_window(now, window):
        return []

    dialogs = await asyncio.to_thread(store.count_dialogs_since,
                                      now - VOLUME_WINDOW_SEC)
    if dialogs < ka.min_dialogs_per_month:
        # Тихому клиенту режим не включается вовсе — прямое решение владельца
        # (§9 п.3). Стоимость пингов фиксирована на клиента, а экономия растёт
        # с объёмом: ниже порога арка уводит клиента В МИНУС, и сумма по
        # портфелю этого не покажет.
        log.debug("keep-alive [%s]: %d диал/мес < порога %d — пропуск",
                  cfg.slug, dialogs, ka.min_dialogs_per_month)
        return []

    touch_tags = [t for tag in TAG_MAP for t in _TOUCH_TAGS[tag]]
    last_touch = await asyncio.to_thread(store.last_llm_usage_ts, touch_tags)
    own_pings = await asyncio.to_thread(_own_ping_ts, store, cfg.slug)
    last_call_ts = {
        tag: max((ts for ts in
                  [last_touch.get(t) for t in _TOUCH_TAGS[tag]] + [own_pings[tag]]
                  if ts is not None), default=None)
        for tag in TAG_MAP}

    results: list[PingResult] = []
    for tag in due_tags(now=now, last_call_ts=last_call_ts, window=window):
        if tag == CLASSIFIER_TAG and not _classifier_prefix_is_cacheable():
            results.append(PingResult(
                tag=tag, sent=False, cache_read=0,
                error="раскладка классификатора под кэш выключена "
                      "(CHATTER_CLASSIFIER_CACHE) — греть нечего"))
            continue
        results.append(await _ping_one(cfg, store, llm, tag,
                                       last_ts=last_call_ts[tag], now=now,
                                       notifier=notifier))
    return results


def flag_ts(raw) -> float | None:
    """Метка времени из `runtime_flags` — или None, если значение НЕ ЧИСЛО.

    Отдельная функция, потому что `float()` числом считает больше, чем человек:
    `float("nan")`, `float("inf")` и `float("1e999")` (переполнение до inf)
    проходят БЕЗ исключения. Наивная проверка через `try/except ValueError` их
    пропускает, и дальше они ведут себя тихо и по-разному в каждом месте:
    сравнение с `nan` всегда ложно, `now - inf` уводит любой возраст в минус.

    Сегодня это отказывает в безопасную сторону — но лишь потому, что операторы
    сравнения оказались теми, какие есть. Это удача, а не замысел: замена `<` на
    `>=` при рефакторинге превратила бы `inf` в способ выключить сторожа
    строкой в `runtime_flags`. Поэтому нечисло отсекается ЗДЕСЬ и одинаково для
    всех читателей флагов, а не в каждом по-своему.

    `math.isfinite` — единственная проверка, ловящая все три случая сразу:
    `nan` не равен сам себе, а `inf` не сравним по величине ни с чем разумным.
    """
    try:
        ts = float(raw)
    except (TypeError, ValueError):
        return None
    return ts if math.isfinite(ts) else None


def _own_ping_ts(store, slug: str) -> dict[str, float | None]:
    """Когда МЫ сами в последний раз грели каждый префикс ЭТОГО клиента.

    Порченое значение флага читается как «пингов не было»: лишний пинг стоит
    $0.0025, а пропущенный — запись по ставке 1h, то есть в 20 раз дороже.
    Молчать при этом нельзя (DEV-18) — иначе флаг, испорченный однажды, тихо
    удвоил бы число пингов навсегда.
    """
    out: dict[str, float | None] = {}
    for tag in TAG_MAP:
        raw = store.get_runtime_flag(ping_flag_key(slug, tag))
        if not raw:
            out[tag] = None
            continue
        ts = flag_ts(raw)
        if ts is None:
            log.warning("keep-alive [%s/%s]: метка своего пинга = %r — не число, "
                        "считаю, что пинга не было", slug, tag, raw)
        out[tag] = ts
    return out


async def _ping_one(cfg, store, llm, tag: str, *, last_ts: float | None,
                    now: float, notifier=None) -> PingResult:
    """Один пинг одного префикса: собрать, отправить, записать, разобрать."""
    try:
        payload = build_ping_payload(cfg, tag)
    except Exception as exc:                                   # noqa: BLE001
        # Сборка префикса упала — это НАША ошибка, не сеть. Громко (DEV-18),
        # но не наружу: keep-alive не имеет права ронять раннер, который в это
        # же время отвечает лиду.
        log.exception("keep-alive [%s/%s]: префикс не собрался — пинг пропущен",
                      cfg.slug, tag)
        return PingResult(tag=tag, sent=False, cache_read=0,
                          error=f"{type(exc).__name__}: {exc}")

    usage_tag = TAG_MAP[tag]
    try:
        # В поток: SDK ходит по сети синхронно, а мы живём в том же event loop,
        # что и ответы лиду. Фоновая оптимизация не имеет права держать петлю.
        rec = await asyncio.to_thread(llm.send_raw, payload, tag=usage_tag)
    except Exception as exc:                                   # noqa: BLE001
        log.warning("keep-alive [%s/%s]: пинг не ушёл (%s: %s) — пропускаю "
                    "цикл, лид не затронут", cfg.slug, tag,
                    type(exc).__name__, exc, exc_info=True)
        return PingResult(tag=tag, sent=False, cache_read=0,
                          error=f"{type(exc).__name__}: {exc}")

    cache_read = int(rec.get("cache_read_input_tokens", 0) or 0)
    write_error = None
    try:
        # Метка СВОЕГО касания — пер-клиентная и отдельная от строки usage:
        # по ней следующий тик поймёт, что запись уже грета, не спрашивая у
        # общего на процесс тега (см. _TOUCH_TAGS). Пишется по факту УДАЧНОЙ
        # отправки: несостоявшийся пинг ничего не трогал.
        await asyncio.to_thread(store.set_runtime_flag,
                                ping_flag_key(cfg.slug, tag), str(now), ts=now)
    except Exception as exc:                                   # noqa: BLE001
        # Не потеря метрики, а РИСК ДЕНЕГ: без метки следующий тик через 5
        # минут пингует ещё раз, и так до конца окна. Поэтому громко.
        write_error = f"метка пинга не записана: {type(exc).__name__}: {exc}"
        log.warning("keep-alive [%s/%s]: %s — следующий тик пингует повторно",
                    cfg.slug, tag, write_error, exc_info=True)
    try:
        # Пишем МЫ, а не usage_sink клиента: sink пишет под тегом вызывающего,
        # и строка ушла бы дважды. Схема при этом не меняется — та же таблица,
        # тот же метод, другой тег (§5 п.3).
        await asyncio.to_thread(store.add_llm_usage, ts=now, **rec)
    except Exception as exc:                                   # noqa: BLE001
        # Пинг УЖЕ отправлен и деньги уже потрачены: врать `sent=False` нельзя.
        # Но потеря строки не мелочь — по ней считается и §4, и решение
        # «пинговать ли», поэтому громко.
        write_error = f"usage не записан: {type(exc).__name__}: {exc}"
        log.warning("keep-alive [%s/%s]: %s", cfg.slug, tag, write_error,
                    exc_info=True)

    if cache_read == 0:
        await _handle_miss(cfg, store, notifier, tag, last_ts=last_ts, now=now)

    return PingResult(tag=tag, sent=True, cache_read=cache_read,
                      error=write_error)


async def _handle_miss(cfg, store, notifier, tag: str, *,
                       last_ts: float | None, now: float) -> str:
    """Порода промаха пинга и что по ней делать (§5).

    Различать породы обязательно: одна из них — наша же регрессия, две другие
    — законная жизнь. Алерт на законной жизни кончается тем же, чем всегда
    красный сторож: его перестают читать и пропускают настоящую поломку.

    Граница между «опоздали» и «сломано» — `CACHE_TTL_SEC`, а НЕ период пинга.
    Таблица §5 называет два числа («> 60 мин» и «< 50 мин») и оставляет
    промежуток между ними неописанным — а нормальный кадэнс пингов ложится
    ровно туда (50–55 мин). Решает физика, а не расписание: запись живёт час
    от последнего касания, значит промах РАНЬШЕ часа — улика, позже — закон.
    Число берётся из `cache_health`, где его читает детектор регрессии: два
    TTL в двух модулях разошлись бы молча.

    Смена конфига внутри разрыва — ТРЕТЬЯ порода, которую таблица §5 не
    называет, а жизнь даёт регулярно: `/reload` меняет плейбук → префикс
    другой → первый пинг по нему ЗАКОННО промахивается (записи ещё нет).
    Без этой ветки каждая правка базы знаний стреляла бы алертом «префикс
    сломан» — тот же довод, по которому `consecutive_misses` считает такой
    промах нейтральным.
    """
    gap = None if last_ts is None else now - last_ts
    if gap is None or gap >= CACHE_TTL_SEC:
        log.warning(
            "keep-alive [%s/%s]: ПРОМАХ пинга (cache_read=0, предыдущее "
            "касание %s назад) — пинг опоздал, TTL истёк законно; "
            "повторяется — чинить цикл, а не поднимать период",
            cfg.slug, tag, "?" if gap is None else f"{gap:.0f}с")
        return "late"

    if await asyncio.to_thread(_config_changed_between, store, last_ts, now):
        log.warning(
            "keep-alive [%s/%s]: промах пинга после СМЕНЫ КОНФИГА — префикс "
            "сменился законно, это первое касание новой записи, алерта нет",
            cfg.slug, tag)
        return "config_changed"

    # «Посчитано» — первый конец класса «посчитано ≠ доехало». Второй конец
    # внутри `_alert_broken_prefix`: там запись о ФАКТЕ доставки.
    log.error(
        "keep-alive [%s/%s]: ПРЕФИКС СЛОМАН — промах пинга через %.0fс после "
        "касания (TTL %.0fс): в кэшируемый блок въехало изменчивое",
        cfg.slug, tag, gap, CACHE_TTL_SEC)
    await _alert_broken_prefix(cfg, store, notifier, tag, gap=gap, now=now)
    return "broken"


def _config_changed_between(store, lo: float | None, hi: float) -> bool:
    """Менялся ли конфиг между двумя моментами.

    Флаг `config_changed_ts` пишет `reload_configs` — тот же источник, по
    которому пульт показывает возраст правки.

    Этот флаг управляет ГАСИТЕЛЕМ алерта, и сломанный гаситель обязан отказывать
    в безопасную сторону. Порченое значение читается как «правки НЕ БЫЛО» —
    алерт уходит владельцу — И отдельной строкой сообщается про сам флаг (§5,
    редакция 20.08). Оба конца обязательны: гасить нельзя, иначе мусор в
    `runtime_flags` становится способом выключить сторожа; и промолчать про
    порчу нельзя, иначе она чинится только тогда, когда сломается что-то ещё.
    «Громко написал в лог ВМЕСТО алерта» тут не смягчающее обстоятельство, а тот
    же отказ: логи никто не читает, ради этого весь §5 и переписан.

    ПУСТОЙ флаг — НЕ порча и предупреждения не даёт. Клиент, ни разу не
    делавший `/reload`, живёт так законно и постоянно; строка на каждом тике у
    каждого такого клиента сделала бы сигнал красным при исправной работе, а
    такие перестают читать вместе с настоящими.
    """
    if lo is None:
        return False
    raw = store.get_runtime_flag("config_changed_ts")
    if not raw:
        return False        # /reload не делали ни разу — это норма, не порча
    ts = flag_ts(raw)
    if ts is None:
        log.warning("keep-alive: config_changed_ts = %r — не число; считаю, "
                    "что правки конфига не было (алерт про сломанный префикс "
                    "уходит владельцу), но сам флаг после reload испорчен", raw)
        return False
    return lo < ts <= hi


async def _alert_broken_prefix(cfg, store, notifier, tag: str, *, gap: float,
                               now: float) -> bool:
    """Довезти до владельца то, что кэш сломан (§5, подраздел о доставке).

    Путь тот же, что у `_maybe_degraded_alert`: `notifier.notify` + карточка в
    `console_cards` + дебаунс через `runtime_flag`. Один `log.warning` здесь не
    годится вовсе: логи никто не читает, и именно так 23.07 смерть кэша
    прожила сутки незамеченной. Арка, молчащая в лог о собственной поломке,
    слепа ровно так же, как было до неё.

    Счётчик СВОЙ. Ни одного `control_event`, который читает
    `classifier_error_threshold`, здесь не пишется (§5 п.2): иначе фоновая
    задача поднимала бы боевой алерт деградации классификатора.

    Дебаунс ОБЯЗАТЕЛЕН и берётся из настроек клиента
    (`control.status_window_hours`, то же окно, что у алерта деградации).
    Сломанный префикс промахивается КАЖДЫЕ 50 минут — без дебаунса это 29
    сообщений в сутки, то есть сторож, которого перестают читать на второй
    день.

    Флаг ставится ТОЛЬКО по факту доставки (AUDIT D4): флаг, поставленный до
    `notify`, сжёг бы окно на сутки на недоставленном алерте — владелец
    остался бы глух, не получив НИ ОДНОГО сообщения.
    """
    control = cfg.settings.control
    window = max(float(control.status_window_hours) * 3600.0, 0.0)
    key = alert_flag_key(cfg.slug, tag)
    last = await asyncio.to_thread(store.get_runtime_flag, key)
    if last:
        # Тот же разбор, что у остальных флагов: `float("inf")` здесь увёл бы
        # возраст алерта в минус и заглушил владельца НАВСЕГДА — одной строкой
        # в `runtime_flags`. Гаситель, сломавшись, обязан перестать гасить.
        last_ts = flag_ts(last)
        if last_ts is None:
            log.warning("keep-alive [%s/%s]: флаг дебаунса = %r — не число, "
                        "алертую", cfg.slug, tag, last)
        elif now - last_ts < window:
            log.info("keep-alive [%s/%s]: алерт о сломанном префиксе "
                     "подавлён дебаунсом (окно %.0fч), поломка В СИЛЕ",
                     cfg.slug, tag, window / 3600.0)
            return False

    if notifier is None:
        # ГРОМКО и отдельной строкой: «посчитано» уже случилось, а «доехало»
        # не случится никогда. Флаг НЕ ставим: дебаунс должен гасить повторы
        # ДОСТАВЛЕННОГО алерта, а не прятать поломку, про которую владелец
        # так и не узнал.
        log.error("keep-alive [%s/%s]: алерт о сломанном префиксе НЕ "
                  "ДОСТАВЛЕН: у клиента нет пульта (notifier=None)",
                  cfg.slug, tag)
        return False

    text = console_text("keepalive_prefix_broken", cfg.settings.language,
                        slug=cfg.slug, tag=tag, gap_min=int(gap // 60))
    try:
        handle = await asyncio.to_thread(notifier.notify, Card(
            kind="alert", contact_id="", text_html=escape_html(text),
            buttons=[], reply_hints=[]))
    except Exception:
        log.exception("keep-alive [%s/%s]: отправка алерта УПАЛА", cfg.slug, tag)
        return False
    if handle is None:
        log.error("keep-alive [%s/%s]: алерт НЕ ДОСТАВЛЕН (notifier вернул "
                  "None) — владелец о поломке НЕ ЗНАЕТ", cfg.slug, tag)
        return False

    await asyncio.to_thread(store.set_runtime_flag, key, str(now), ts=now)
    try:
        # Карточка в `console_cards` — тот же след, что у эскалаций.
        # `contact_id` пустой честно: алерт не про лида, а про клиента
        # целиком. Ответ владельца на такую карточку не адресует чужой
        # диалог: `card_contact` вернёт пустую строку, а разбор команды
        # трактует её как «карточка не найдена».
        msg_id = int(handle.ref.split(":")[-1])
        await asyncio.to_thread(store.add_card, msg_id=msg_id, contact_id="",
                                kind="alert", ts=now)
    except Exception:
        log.warning("keep-alive [%s/%s]: карточка алерта не записана (%r)",
                    cfg.slug, tag, handle, exc_info=True)
    # Второй конец класса «посчитано ≠ доехало»: факт доставки записан
    # ОТДЕЛЬНО от факта решения алертовать.
    log.warning("keep-alive [%s/%s]: алерт о сломанном префиксе ДОСТАВЛЕН "
                "владельцу (%s)", cfg.slug, tag, handle.ref)
    return True
