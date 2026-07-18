from __future__ import annotations
from chatter.config.loader import Config
from chatter.core.llm import LLMClient

_LANG_NAME = {"ru": "русском", "en": "английском", "uk": "украинском"}

_STYLE = (
    "Пиши как живой человек в личной переписке, а не как корпоративный ассистент. "
    "БЕЗ списков, БЕЗ заголовков, БЕЗ markdown-разметки и канцелярита. "
    "Не открывай сообщение меню вариантов на выбор. "
    "Не выдумывай цены, сроки и скидки, которых нет в разделе ЗНАНИЯ — если чего-то "
    "не знаешь, так и скажи: уточнишь или позовёшь владельца, но не выдумывай. "
    "Если ответ на вопрос ЕСТЬ в разделе ЗНАНИЯ — ответь сразу и по существу, "
    "приведи факт из ЗНАНИЯ прямо сейчас. Не отвечай «уточню и вернусь», когда "
    "ответ уже есть в ЗНАНИЯ; откладывай («уточню и вернусь») только когда в "
    "ЗНАНИЯ этого правда нет. "
    "Следуй голосу персоны, как он описан в разделе ПЕРСОНА: её тону, степени "
    "формальности, обычной длине сообщений, использованию эмодзи и тому, как часто "
    "она задаёт уточняющие вопросы. Встречный вопрос — это нормально, но не "
    "обязателен в каждом сообщении: иногда достаточно просто ответить. "
    "Если спросят, бот ты, ИИ или живой человек — ответь честно, что ты "
    "виртуальный ассистент: никогда не выдавай себя за живого человека."
)


def build_system_prompt(cfg: Config) -> str:
    lang = _LANG_NAME.get(cfg.settings.language, "русском")
    return (
        f"Ты ведёшь личную переписку от лица персоны. Отвечай на {lang} языке.\n\n"
        f"=== ПЕРСОНА ===\n{cfg.persona}\n\n"
        f"=== ЗНАНИЯ (товар, прайс, условия, FAQ) ===\n{cfg.knowledge}\n\n"
        f"=== ПЛЕЙБУК (воронка, цели, чего не обещать) ===\n{cfg.playbook}\n\n"
        f"=== ПРАВИЛА ===\n{_STYLE}"
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
        system = self._system
        if context_note:
            system = (
                f"{self._system}\n\n"
                f"=== КОНТЕКСТ ОТВЕТА (разовая заметка, не часть персоны) ===\n{context_note}"
            )
        return self._llm.complete(
            system,
            build_messages(history),
            max_tokens=self._cfg.settings.limits.max_reply_tokens,
        )
