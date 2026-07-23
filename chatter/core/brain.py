from __future__ import annotations

import logging

from chatter.config.loader import HONESTY_HONEST, Config
from chatter.core.llm import LLMClient

log = logging.getLogger("chatter.core.brain")

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

    def reply(self, history: list[dict], *, context_note: str | None = None) -> str:
        """`context_note`: an optional ONE-OFF instruction for this reply only
        (e.g. "this message waited 20 min, acknowledge the pause in your own
        words"). It rides in the system prompt for this single call but is NOT
        part of the persona -- absent by default, so existing behaviour is
        unchanged."""
        # Разовая заметка уходит uncached_suffix-ом: стабильная система
        # кэшируется (cache_control в AnthropicLLM), заметка — отдельным
        # блоком ПОСЛЕ breakpoint'а, кэш не инвалидируется.
        suffix = (
            f"=== КОНТЕКСТ ОТВЕТА (разовая заметка, не часть персоны) ===\n{context_note}"
            if context_note else None
        )
        return self._llm.complete(
            self._system,
            build_messages(history),
            max_tokens=self._cfg.settings.limits.max_reply_tokens,
            uncached_suffix=suffix,
            tag="brain",
        )
