# Спека: prompt-caching + usage-логгер (Спринт 0 п.2)

**Статус:** черновик, ждёт ОК Даниила. **Скоуп:** ROADMAP п.2 без окна истории
(окно — отдельная задача, кэш системы от него не зависит: история идёт в
`messages` ПОСЛЕ breakpoint'а и кэш системы не трогает).

## Факты (из кода и API-доков)

- Кэш Anthropic = префикс-матч: `tools → system → messages`; breakpoint =
  `cache_control: {"type": "ephemeral"}` на блоке. Write ×1.25, read ×0.1,
  TTL 5 мин (продлевается каждым чтением).
- Верификация ТОЛЬКО по `usage.cache_read_input_tokens` из ответа API
  (критерий Даниила). `cache_creation_… = 0` при живом маркере = префикс
  короче минимума модели (молча, без ошибки): у haiku-4-5 минимум 4096
  токенов, у sonnet-семейства 1024–2048.
- Наши вызовы: `AnthropicLLM.complete()` — единая точка (brain + классификатор).
  Оба системных промпта стабильны в течение процесса (persona/knowledge/playbook;
  меняются только на /reload → одна инвалидация, норм). Таймстампов/UUID в
  промптах нет — тихих инвалидаторов не обнаружено.
- ⚠️ Единственный волатильный кусок: `context_note` брейна (разовая заметка)
  сейчас КОНКАТЕНИРУЕТСЯ в хвост system-строки → при кэшировании всей строки
  каждая заметка инвалидировала бы кэш. Решение ниже.

## Дизайн

### 1. Кэшируемый префикс (`chatter/core/llm.py`)

`LLMClient.complete()` получает новый keyword `uncached_suffix: str | None = None`
(ABC + FakeLLM + AnthropicLLM — сигнатуры совпадают, DEV-19).

`AnthropicLLM.complete` шлёт system списком блоков:

```python
system_blocks = [{"type": "text", "text": system,
                  "cache_control": {"type": "ephemeral"}}]
if uncached_suffix:
    system_blocks.append({"type": "text", "text": uncached_suffix})
```

- `Brain.reply`: `context_note` уходит в `uncached_suffix` (стабильная часть
  кэшируется, заметка — после breakpoint, кэш жив).
- Классификатор: система стабильна целиком, suffix не нужен.
- TTL — дефолтные 5 мин (write ×1.25). 1h (write ×2) не берём: живой лид
  отвечает чаще, чем раз в 5 минут; решение пересмотрим по замеру.
- FakeLLM пишет suffix в `calls` — офлайн-тесты видят контракт.

### 2. Usage-логгер (`chatter/storage/db.py` + `llm.py`)

- Новая таблица `llm_usage(id, ts, tag, model, input_tokens, output_tokens,
  cache_read_input_tokens, cache_creation_input_tokens)` — паттерн
  CREATE TABLE IF NOT EXISTS, как соседние.
- `AnthropicLLM(model, usage_sink=None)`: sink — `Callable[[dict], None]`;
  после каждого ответа собирает 4 поля usage + model + tag и зовёт sink.
  Сбой sink НЕ роняет ответ, но логируется `log.warning` (DEV-18 — не молча).
- `complete(..., tag: str = "")`: brain зовёт с `tag="brain"`, классификатор —
  `tag="classifier"` (это правки вызовов в `brain.py`/`classifier.py`).
- Wiring: в трёх местах постройки `AnthropicLLM` (`run.py:518`,
  `telethon_run.py:1462`, `demo_switch.py:82`) sink пишет в клиентскую БД
  (`.secrets/<slug>.db`) через store.

### 3. Замер (по факту, не по прайсу)

1. Деплой → реальный дрил-диалог (N реплик, allowlist, гейт закрыт).
2. SQL по `llm_usage`: суммы 4 полей по tag.
3. «До» = эквивалент без кэша: `input + cache_read + cache_creation` по полной
   цене. «После» = факт: `input×1.0 + cache_creation×1.25 + cache_read×0.1`.
   Экономия = разница; отчёт с сырыми числами токенов.
4. Отдельно проверяем `cache_creation > 0` на первом вызове (порог минимума
   префикса пройден) и `cache_read > 0` на последующих. Если 0 — промпт короче
   минимума модели: факт в отчёт, решение отдельно (не маскировать).

## Не входит (bэклог)

- Окно истории (unbounded рост промпта) — отдельная задача.
- Кэш-breakpoint на хвосте `messages` (кэш истории диалога) — после замера,
  если системный кэш не даст целевой экономии.
- Расход $ в дайджест владельцу (H4/М6) — логгер этому фундамент.

## Риски

- `no_thinking` (thinking disable) инвалидирует только messages-tier кэша,
  system-кэш живёт — брейн и классификатор и так разные кэш-записи.
- sonnet-5: новый токенизатор, но порог минимума — вероятно 2048; проверяется
  замером, не гаданием.
- /reload и правка knowledge инвалидируют кэш один раз — ожидаемо и дёшево.

## Дополнения (ОК Даниила 2026-07-23)

- **Порог кэша ДО дрила:** посчитать арифметикой (count_tokens) кэшируемый
  префикс каждого клиента против минимума провайдера — volska (sonnet-5) и
  demo (haiku-4-5, порог 4096). Префикс короче порога — сказать до замера.
  Замер экономии — на volska (боевая).
- **TTL-данные:** `ts` в llm_usage + существующая `messages` дают реальные
  интервалы между сообщениями диалога. После замера — доля вызовов в
  cache_read vs промахи из-за 5-мин TTL (лид вернулся через 20-40 мин =
  полный re-write). Решение по 1h TTL (write ×2) — по этим цифрам, не сейчас.
- Сбой sink: не роняет ответ + log.warning — подтверждено, тихих деградаций нет.

## Порядок

TDD в worktree (ветка `sprint0/prompt-caching`), 955 chatter green + новые,
деплой = мердж в phase-4.0 + рестарт раннера гардианом, живой замер, отчёт.
