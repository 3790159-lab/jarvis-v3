from __future__ import annotations
from chatter.config.loader import Config
from chatter.core.llm import LLMClient

_LANG_NAME = {"ru": "русском", "en": "английском", "uk": "украинском"}

_STYLE = (
    "Стиль ответа жёстко: 1-2 коротких предложения. БЕЗ списков, БЕЗ заголовков, "
    "БЕЗ канцелярита и корпоративного тона. Пиши как живой человек в личке. "
    "Задавай встречные вопросы, чтобы вести диалог. "
    "Не выдумывай цены, сроки и скидки, которых нет в разделе ЗНАНИЯ."
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
            max_tokens=self._cfg.settings.limits.max_tokens_per_dialog,
        )
