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
    "Следуй голосу персоны, как он описан в разделе ПЕРСОНА: её тону, степени "
    "формальности, обычной длине сообщений, использованию эмодзи и тому, как часто "
    "она задаёт уточняющие вопросы. Встречный вопрос — это нормально, но не "
    "обязателен в каждом сообщении: иногда достаточно просто ответить."
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

    def reply(self, history: list[dict]) -> str:
        return self._llm.complete(
            self._system,
            build_messages(history),
            max_tokens=self._cfg.settings.limits.max_reply_tokens,
        )
