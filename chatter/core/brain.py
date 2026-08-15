from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from chatter.config.loader import HONESTY_HONEST, Config
from chatter.core.llm import LLMClient
from chatter.core import prompt_log

log = logging.getLogger("chatter.core.brain")

# Бот раніше не знав поточного часу (Ольга привіталася «Добрий день»
# ввечері). Єдина таймзона для всіх клієнтів — TODO: per-client timezone,
# коли з'явиться клієнт поза Europe/Kyiv.
KYIV_TZ = ZoneInfo("Europe/Kyiv")

_WEEKDAYS_UK = ("понеділок", "вівторок", "середа", "четвер", "п'ятниця",
               "субота", "неділя")

# М8: бюджет секции примеров в символах (~1.5k токенов при ~4 симв./токен).
# Пары включаются по порядку, пока влезают; хвост отбрасывается С WARNING.
EXAMPLES_CHAR_BUDGET = 6000

_LANG_NAME = {"ru": "русском", "en": "английском", "uk": "украинском"}

_STYLE_BASE = (
    "Пиши как живой человек в личной переписке, а не как корпоративный ассистент. "
    "БЕЗ списков, БЕЗ заголовков, БЕЗ markdown-разметки и канцелярита. "
    "Не открывай сообщение меню вариантов на выбор. "
)

# strict_knowledge=True (дефолт): отвечаем только из базы.
_STYLE_STRICT_KNOWLEDGE = (
    "Не выдумывай цены, сроки и скидки, которых нет в разделе ЗНАНИЯ — если чего-то "
    "не знаешь, так и скажи: уточнишь или позовёшь владельца, но не выдумывай. "
)

# strict_knowledge=False: свободнее по существу, но ЦИФРЫ по-прежнему под замком.
# Инструкция обязана совпадать с пост-фильтром (unbacked_claim подавляет
# выдуманные цены в обоих режимах) — иначе модель уверенно генерировала бы то,
# что код всё равно глушит, и владелец видел бы поток подавлений без причины.
_STYLE_FREE_KNOWLEDGE = (
    "Опирайся на раздел ЗНАНИЯ, но можешь отвечать и шире — на общие вопросы по теме, "
    "по-человечески, не упираясь только в базу. При этом конкретные цены, сроки и "
    "размеры скидок не выдумывай никогда: если точной цифры нет в разделе ЗНАНИЯ, "
    "не называй её, а скажи, что уточнишь. "
)

_STYLE_PERSONA = (
    "Следуй голосу персоны, как он описан в разделе ПЕРСОНА: её тону, степени "
    "формальности, обычной длине сообщений, использованию эмодзи и тому, как часто "
    "она задаёт уточняющие вопросы. Встречный вопрос — это нормально, но не "
    "обязателен в каждом сообщении: иногда достаточно просто ответить. "
)

# honesty_mode=honest (дефолт). В свободном режиме строка НЕ добавляется вовсе:
# оставить её значило бы столкнуть промпт («признайся честно») с конфигом («не
# обязана») — модель металась бы между ними, а владелец не понимал, что включено.
_STYLE_HONESTY = (
    "Если спросят, бот ты, ИИ или живой человек — ответь честно, что ты "
    "виртуальный ассистент: никогда не выдавай себя за живого человека."
)


def build_style(cfg: Config) -> str:
    """Раздел ПРАВИЛА, собранный под per-client тумблеры."""
    parts = [
        _STYLE_BASE,
        _STYLE_STRICT_KNOWLEDGE if cfg.settings.strict_knowledge else _STYLE_FREE_KNOWLEDGE,
        _STYLE_PERSONA,
    ]
    if cfg.settings.honesty_mode == HONESTY_HONEST:
        parts.append(_STYLE_HONESTY)
    return "".join(parts).strip()


def _examples_section(cfg: Config) -> str:
    """М8: эталонные пары голоса. Стабильны между /reload → живут внутри
    кэшируемого префикса. Отбор с бюджетом: пары по порядку до
    EXAMPLES_CHAR_BUDGET, хвост громко отбрасывается (не молча, DEV-18)."""
    if not cfg.examples:
        return ""
    header = (
        "\n\n=== ПРИКЛАДИ ДІАЛОГІВ (еталон голосу) ===\n"
        "Наслідуй СТИЛЬ і СТРУКТУРУ цих відповідей (вилка + питання, наступний "
        "крок у кожній репліці). Факти, ціни й терміни бери ТІЛЬКИ з розділу "
        "ЗНАННЯ — приклади задають голос, не цифри.\n")
    used = 0
    parts: list[str] = []
    dropped = 0
    for client, olga in cfg.examples:
        chunk = f"\nКлієнт: {client}\nТи: {olga}\n"
        if used + len(chunk) > EXAMPLES_CHAR_BUDGET:
            dropped += 1
            continue
        used += len(chunk)
        parts.append(chunk)
    if dropped:
        log.warning(
            "examples.yaml: %d пар(ы) не влезли в бюджет %d символов и "
            "отброшены — сократите примеры", dropped, EXAMPLES_CHAR_BUDGET)
    if not parts:
        return ""
    return header + "".join(parts)


def _day_part(hour: int) -> str:
    """Словесна частина доби за годиною (0-23), Europe/Kyiv."""
    if 5 <= hour < 11:
        return "ранок"
    if 11 <= hour < 17:
        return "день"
    if 17 <= hour < 22:
        return "вечір"
    return "ніч"


def build_time_block(now: datetime | None = None) -> str:
    """Поточний час — дата, день тижня, час і частина доби. ТІЛЬКИ для
    uncached_suffix (ПІСЛЯ cache-breakpoint'а): значення міняється щохвилини,
    а стабільний префікс не можна чіпати ні байтом (регресія 23.07 — мінливе
    в префіксі вбиває кеш). `now`: інʼєкція для тестів; за замовчуванням —
    реальний поточний момент."""
    dt = (now or datetime.now(KYIV_TZ)).astimezone(KYIV_TZ)
    weekday = _WEEKDAYS_UK[dt.weekday()]
    return (
        "=== ПОТОЧНИЙ ЧАС (Europe/Kyiv) ===\n"
        f"Зараз {weekday}, {dt.strftime('%d.%m.%Y')}, {dt.strftime('%H:%M')} "
        f"({_day_part(dt.hour)})."
    )


def build_system_prompt(cfg: Config) -> str:
    lang = _LANG_NAME.get(cfg.settings.language, "русском")
    return (
        f"Ты ведёшь личную переписку от лица персоны. Отвечай на {lang} языке.\n\n"
        f"=== ПЕРСОНА ===\n{cfg.persona}\n\n"
        f"=== ЗНАНИЯ (товар, прайс, условия, FAQ) ===\n{cfg.knowledge}\n\n"
        f"=== ПЛЕЙБУК (воронка, цели, чего не обещать) ===\n{cfg.playbook}\n\n"
        f"=== ПРАВИЛА ===\n{build_style(cfg)}"
        f"{_examples_section(cfg)}"
    )


def build_messages(history: list[dict]) -> list[dict]:
    return [{"role": m["role"], "content": m["text"]} for m in history]


class Brain:
    def __init__(self, llm: LLMClient, cfg: Config):
        self._llm = llm
        self._cfg = cfg
        self._system = build_system_prompt(cfg)

    def reply(self, history: list[dict], *, context_note: str | None = None,
              profile: str | None = None, obligations_block: str = "",
              obligations=(), log_shape: bool = False, contact_id: str = "",
              now: datetime | None = None, invoice_block: str = "") -> str:
        """`context_note`: an optional ONE-OFF instruction for this reply only
        (e.g. "this message waited 20 min, acknowledge the pause in your own
        words"). It rides in the system prompt for this single call but is NOT
        part of the persona -- absent by default, so existing behaviour is
        unchanged.

        `obligations_block`: рендер слота обязательств (спека 2026-07-24 §5),
        уже с заголовками. Пусто по умолчанию → поведение как раньше (флаг
        CHATTER_OBLIGATIONS_SLOT off). Едет тем же uncached_suffix-ом, что и
        профиль, но рендерится из ТАБЛИЦЫ (не из окна) → долг доезжает, даже
        когда ход-источник уехал за окно истории.

        `now`: інʼєкція поточного часу для тестів (детермінізм); за
        замовчуванням — реальний Europe/Kyiv-момент (build_time_block)."""
        # Профиль лида и разовая заметка уходят uncached_suffix-ом: стабильная
        # система кэшируется (cache_control в AnthropicLLM) И ОБЩАЯ для всех
        # контактов; per-contact профиль — отдельным блоком ПОСЛЕ breakpoint'а,
        # кэш не инвалидируется, профиль всегда самый свежий (арка «память»).
        # Блок часу — туда ж і з тієї ж причини: міняється щохвилини, у
        # стабільному префіксі вбив би кеш (регресія 23.07).
        parts = [build_time_block(now)]
        if profile:
            parts.append(
                f"=== ПРОФІЛЬ КЛІЄНТА (з минулих розмов; актуальні факти) ===\n{profile}")
        if obligations_block:
            parts.append(obligations_block)
        if invoice_block:
            # ОТДЕЛЬНЫЙ блок, а не строка слота: слот инъектит только
            # `owed_by=bot`, а долг оплаты лежит на КЛИЕНТЕ — без своего блока
            # модель о неоплаченном счёте не узнала бы вовсе (§14 п.13). Едет
            # тем же uncached_suffix-ом: статус счёта меняется, а изменчивое в
            # стабильном префиксе убивает кэш.
            parts.append(invoice_block)
        if context_note:
            parts.append(
                f"=== КОНТЕКСТ ОТВЕТА (разовая заметка, не часть персоны) ===\n{context_note}")
        suffix = "\n\n".join(parts) or None
        # §8: структурная строка (PII-free) + полный дамп по флагу. Гейтится
        # log_shape (=slot on) → при выключенной арке тишина, byte-identical.
        if log_shape:
            prompt_log.log_prompt_shape(
                system=self._system, suffix=suffix, obligations=obligations,
                tag="brain", contact_id=contact_id)
        return self._llm.complete(
            self._system,
            build_messages(history),
            max_tokens=self._cfg.settings.limits.max_reply_tokens,
            uncached_suffix=suffix,
            tag="brain",
        )
