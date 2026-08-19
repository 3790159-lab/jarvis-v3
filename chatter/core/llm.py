from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Callable

from chatter.core.prompt_log import log_usage_shape

log = logging.getLogger("chatter.core.llm")

# ── ПОРОГ ВКЛЮЧЕНИЯ PROMPT-КЭША: ОДНА ТАБЛИЦА НА ВЕСЬ ПРОДУКТ (спека §2.2) ──
#
# Порог ЗАВИСИТ ОТ МОДЕЛИ, и это не мелочь тарифа: промпт короче порога
# НЕ КЭШИРУЕТСЯ МОЛЧА. Исключения нет, лога нет, единственный признак —
# `usage.cache_creation_input_tokens == 0`. Следствие контринтуитивное: перевод
# на более дешёвую модель может УВЕЛИЧИТЬ счёт, потому что потеря чтения по
# 0.1× дороже, чем втрое дешёвый токен.
#
# Таблица здесь ОДНА на весь код (§2.2 «не литералом в трёх местах»): числа,
# размноженные по местам, расходятся молча, и меньшее гасит большее. Сторож
# порога (`chatter.core.prefix_budget`) и онбординг читают ЭТУ таблицу.
CACHE_MIN_PROMPT_TOKENS: dict[str, int] = {
    "claude-haiku": 4096,
    "claude-sonnet": 1024,
    "claude-opus": 1024,
}


def cache_min_prompt_tokens(model: str) -> int | None:
    """Порог кэша ДЛЯ ЭТОЙ модели; None — семейство незнакомо.

    None, а НЕ умолчание. Умолчание на месте незнакомой модели дало бы сторожа,
    который «успешно проверил» новую модель по чужому числу, — то есть зелёную
    ширму ровно там, где её ставить опаснее всего: у модели, порога которой
    никто не смотрел. Незнакомая модель обязана быть слышна, а не пройти.
    """
    m = (model or "").strip().lower()
    # Семейство — самый ДЛИННЫЙ подошедший ключ: если завтра появится
    # "claude-haiku-5" со своим порогом, точный ключ обязан победить общий.
    hits = [k for k in CACHE_MIN_PROMPT_TOKENS if m.startswith(k)]
    return CACHE_MIN_PROMPT_TOKENS[max(hits, key=len)] if hits else None



class LLMClient(ABC):
    # no_thinking: явно заглушить расширенное мышление модели. Нужен служебным
    # вызовам с маленьким max_tokens (классификатор): у sonnet-5 thinking включён
    # ПО УМОЛЧАНИЮ при опущенном параметре и молча съедает весь бюджет токенов —
    # инцидент volska 2026-07-22 (пустой ответ классификатора на каждом
    # сообщении). Ответы персоне дефолт модели не трогают.
    #
    # uncached_suffix: волатильный хвост системного промпта (разовая заметка
    # brain'а). Идёт ОТДЕЛЬНЫМ system-блоком ПОСЛЕ cache_control-breakpoint'а —
    # иначе каждая заметка инвалидировала бы кэш всей системы (спека
    # 2026-07-23-chatter-prompt-caching).
    #
    # tag: метка вызывающего ("brain"/"classifier") для usage-логгера.
    @abstractmethod
    def complete(self, system: str, messages: list[dict], *,
                 max_tokens: int, no_thinking: bool = False,
                 uncached_suffix: str | None = None, tag: str = "") -> str:
        ...


class FakeLLM(LLMClient):
    """Deterministic, offline LLM for tests and no-key demo runs."""

    def __init__(self, scripted: list[str] | None = None):
        self._scripted = list(scripted) if scripted else []
        self._i = 0
        self.calls: list[dict] = []
        # Тесты обрезки: список stop_reason'ов по порядку вызовов
        # (None/исчерпан = end_turn). Реальный клиент пишет last_stop_reason
        # из ответа API.
        self.scripted_stop_reasons: list[str] = []
        self.last_stop_reason: str | None = None

    def complete(self, system: str, messages: list[dict], *,
                 max_tokens: int, no_thinking: bool = False,
                 uncached_suffix: str | None = None, tag: str = "") -> str:
        # Наблюдаемый контракт для тестов прежний: suffix — часть system
        # (в реальном запросе он второй system-блок), плюс новые поля отдельно.
        recorded_system = (f"{system}\n\n{uncached_suffix}"
                           if uncached_suffix else system)
        self.calls.append({"system": recorded_system, "messages": messages,
                           "max_tokens": max_tokens, "no_thinking": no_thinking,
                           "uncached_suffix": uncached_suffix, "tag": tag})
        call_idx = len(self.calls) - 1
        self.last_stop_reason = (self.scripted_stop_reasons[call_idx]
                                 if call_idx < len(self.scripted_stop_reasons)
                                 else "end_turn")
        if self._i < len(self._scripted):
            out = self._scripted[self._i]
            self._i += 1
            return out
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        return f"Поняла вас про «{last_user}». Расскажите чуть подробнее, что именно ищете?"


class AnthropicLLM(LLMClient):
    """Production client — model id from config. Reads ANTHROPIC_API_KEY from
    env. The `anthropic` SDK is imported lazily inside __init__ so importing
    this module (and using FakeLLM) never requires the SDK or a network/key.

    Prompt-caching: стабильная система уходит system-блоком с
    `cache_control: ephemeral, ttl 1h` (write ×2 / read ×0.1; решение
    Даниила 2026-07-23 по замеру: 23% интервалов лида в окне 5мин–1ч,
    break-even 0.65 возврата/диалог).
    Порог включения кэша зависит от модели (haiku-4-5: 4096 токенов) —
    короткий промпт молча не кэшируется, факт виден по
    usage.cache_creation_input_tokens == 0.

    usage_sink: колбэк с полями usage каждого вызова (для llm_usage в БД).
    Сбой sink НЕ роняет ответ, но кричит в лог (DEV-18: не молча)."""

    def __init__(self, model: str,
                 usage_sink: Callable[[dict], None] | None = None):
        import anthropic  # imported lazily so tests never need the SDK/key
        self._client = anthropic.Anthropic()
        self._model = model
        self._usage_sink = usage_sink
        # stop_reason ПОСЛЕДНЕГО вызова: вызывающие, для которых обрезка =
        # ошибка (классификатор), проверяют его после complete(). Атрибут,
        # а не изменение сигнатуры: возврат complete() везде остаётся str.
        self.last_stop_reason: str | None = None

    def complete(self, system: str, messages: list[dict], *,
                 max_tokens: int, no_thinking: bool = False,
                 uncached_suffix: str | None = None, tag: str = "") -> str:
        kwargs = {}
        if no_thinking:
            # sonnet-5 и haiku-4-5 оба принимают disabled (проверено живьём
            # 2026-07-22). НЕ слать thinking по умолчанию: адаптивный дефолт
            # модели для ответов персоны — осознанный выбор.
            # Кэшу системы это не мешает: thinking-инвалидация бьёт только
            # messages-tier, system-tier живёт.
            kwargs["thinking"] = {"type": "disabled"}
        system_blocks = [{"type": "text", "text": system,
                          "cache_control": {"type": "ephemeral", "ttl": "1h"}}]
        if uncached_suffix:
            system_blocks.append({"type": "text", "text": uncached_suffix})
        resp = self._client.messages.create(
            model=self._model, max_tokens=max_tokens, system=system_blocks,
            messages=messages, **kwargs,
        )
        self.last_stop_reason = getattr(resp, "stop_reason", None)
        self._record_usage(resp, tag)
        return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()

    @staticmethod
    def _cache_creation_split(u) -> tuple[int, int]:
        """(5m, 1h) из `usage.cache_creation`. Разбивка нужна, потому что ставки
        записи РАЗНЫЕ ($3.75/M против $6/M), а суммарный
        `cache_creation_input_tokens` их не различает — по нему нельзя сказать,
        занижает ли тарифная модель счёт (арка «кэш классификатора», фаза 0).

        Отсутствие поля НЕ ошибка: старый SDK/мок его не отдаёт, а сумма нам
        всё равно известна. Тогда (0, 0) — «разбивки нет», а не «ноль записи»;
        различает их сумма, лежащая в соседней колонке."""
        cc = getattr(u, "cache_creation", None)
        if cc is None:
            return 0, 0
        return (int(getattr(cc, "ephemeral_5m_input_tokens", 0) or 0),
                int(getattr(cc, "ephemeral_1h_input_tokens", 0) or 0))

    def _record_usage(self, resp, tag: str) -> None:
        if self._usage_sink is None:
            return
        try:
            u = resp.usage
            m5, h1 = self._cache_creation_split(u)
            rec = {
                "tag": tag, "model": self._model,
                "input_tokens": int(getattr(u, "input_tokens", 0) or 0),
                "output_tokens": int(getattr(u, "output_tokens", 0) or 0),
                "cache_read_input_tokens":
                    int(getattr(u, "cache_read_input_tokens", 0) or 0),
                "cache_creation_input_tokens":
                    int(getattr(u, "cache_creation_input_tokens", 0) or 0),
                "cache_creation_5m": m5,
                "cache_creation_1h": h1,
            }
            # Строка в лог ДО записи в БД: наблюдаемость не должна зависеть от
            # того, доехал ли sink (спека 2026-07-25 §6 — регрессия 23.07 жила
            # ровно в слепой зоне «что доехало в кэш»).
            log_usage_shape(rec)
            self._usage_sink(rec)
        except Exception:
            # Логгер — наблюдаемость, не бизнес-путь: ответ лиду важнее записи
            # метрики. Но тихо глотать нельзя (DEV-18).
            log.warning("usage-sink упал — запись usage потеряна, ответ не тронут",
                        exc_info=True)
