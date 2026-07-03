# Спека: выровнять одиночные команды с batch

**Дата:** 2026-07-03
**Ветка:** phase-4.0-unified-jarvis (@d63c9bd)
**Статус:** черновик → СТОП, жду ОК

---

## 1. Контекст и проблема

В batch-потоке (`/swapbatch`) у пользователя есть три удобства, которых нет в
одиночных командах `/animate` и `/videoref`:

1. **Свой motion-промт** — batch умеет принять произвольную motion-строку
   (`/swapbatch_set_prompt` → `sess.motion_prompt`). Одиночные команды — нет:
   `/animate` жёстко подставляет `motion=""`
   (`tools/jarvis_smart_telegram_control.py:2051`), `/videoref` умеет только
   платный Grok-анализ (`vref:motion`).
2. **Тогглы качества/длины** — batch рисует caps-aware кнопки
   (`build_quality_keyboard`, префикс `sbq:`). Одиночный `/animate` запускается
   сразу на дефолтах движка (`caps.default_duration/default_resolution`), выбора
   длины/разрешения нет.
3. **Косметика** — в клавиатуре `/videoref` кнопка движка и кнопки длительности
   используют один и тот же эмодзи `🎬` (строки 1438 и 1454) — визуально
   сливаются.

Цель арки — поднять одиночные команды до паритета с batch по этим трём осям,
**не трогая** existing-поведение, batch-поток и гейты трат.

---

## 2. Область

**Входит:**
- Новый seam `app/services/block_m2_video/prompt_intake.py` (приём одной
  свободной motion-строки, чистый/текстовый/бесплатный).
- Подключение seam к `/animate` и `/videoref`.
- Перенос паттерна тогглов качества/длины (`sbq:`) в одиночный `/animate`.
- Смена эмодзи кнопки движка `🎬 → ⚙️` в клавиатуре `/videoref`.

**НЕ трогаем (инвариант):**
- Existing-команды и их поведение.
- `/animate_batch` — гэпа НЕТ (та же orchestrator-сессия,
  `/swapbatch_set_prompt`, `sess.motion_prompt`); в арку не входит (решение b).
- Batch-поток B (нумерованные промты, `_swapbatch_text_intercept`).
- `prompt_assembly.py` (только читаем `clamp_prompt`).
- Гейты трат (`check_limit`/`record_cost`) — не переписываем.

**Money-safe:** seam чисто текстовый и бесплатный; ручной промт в `/videoref`
намеренно НЕ вызывает платный Grok (решение a) → добавляет ноль стоимости.

---

## 3. Принятые решения (фиксация из разведки)

- **(a) /videoref:** ручной промт сосуществует с AI-анализом, приоритет —
  ручного. Если motion задан вручную, платный `generate_video_motion_prompt`
  (Grok) **НЕ вызывается**; ручной путь зовёт только бесплатную часть
  (`select_best_frame` — выбор кадра без Grok). На клавиатуре/в сообщении видно,
  что свой промт задан.
- **(b) /animate_batch:** гэпа нет, в арку не входит.
- **(c) Перехват текста:** `_swapbatch_text_intercept` проверяется **ПЕРВЫМ**,
  `prompt_intake`-перехват — после. Инвариант: двойное ожидание (batch-FSM ждёт
  текст И prompt_intake ждёт → оба консьюмят одно сообщение) недопустимо.

---

## 4. Архитектура seam `prompt_intake.py`

Модульное состояние: `_AWAITING: dict[int, str]` — `chat_id → kind`
(`"animate"` | `"videoref"`).

```python
def arm(chat_id: int, kind: str) -> None: ...
def is_awaiting(chat_id: int) -> bool: ...
def awaiting_kind(chat_id: int) -> str | None: ...   # для label «промт задан»
def disarm(chat_id: int) -> None: ...
def consume(chat_id: int, text: str) -> ConsumeResult | None: ...
```

- `consume` возвращает `None`, если чат не в ожидании (роутер идёт дальше).
- Иначе: снимает ожидание (`disarm`), стрипает текст, применяет
  `clamp_prompt(text, cap)` с `cap = int(os.getenv("WAVESPEED_PROMPT_MAX_CHARS", "1500"))`,
  возвращает `ConsumeResult(kind, motion, truncated)`.
- `motion == ""` после стрипа → `ConsumeResult(..., motion="")`; **роутер/
  control-слой** решает игнорировать пустой (не подставлять) — seam не знает про
  pending-словари.

Seam **не импортирует** ничего платного (ни Grok, ни движки) — только
`clamp_prompt` из `prompt_assembly`. Контрол-слой сам пишет `motion` в
`_ANIMATE_PENDING` / запускает ручной videoref-путь по `kind`.

---

## 5. Задачи (TDD-план)

Порядок TDD везде: **спай-зуб/провальный тест первым → минимальная реализация →
мутация в обе стороны**.

### T1 — seam `prompt_intake.py`
**Тесты (`tests/test_prompt_intake.py`):**
- `is_awaiting` False до arm, True после `arm(chat, "animate")`.
- `consume` не в ожидании → `None` (роутер не ломается).
- `consume` после arm → `ConsumeResult(kind="animate", ...)` и `is_awaiting`
  снова False (снял ожидание). **Мутация:** не снимать ожидание → тест падает.
- **Cap-зуб:** текст длиной `cap+50` → `motion` усечён до `≤ cap`,
  `truncated=True`. **Мутация:** cap на границе `cap` vs `cap-1`.
- Whitespace-only текст → `motion == ""` (control-слой проигнорирует).
- Изоляция по чатам: arm(A) не делает `is_awaiting(B)` True.
- **Money-зуб:** статически — модуль не импортирует `motion_prompt_ai`/движки
  (grep-guard в тесте или assert по `sys.modules` после import).

### T2 — хук перехвата в текст-роутере + инвариант (решение c)
**Точка:** `tools/jarvis_smart_telegram_control.py:~7636` — между
`_swapbatch_text_intercept` и `_route_plain_text`:
```python
if _member and _swapbatch_text_intercept(chat_id, text):
    return
if _member and _prompt_intake_intercept(chat_id, text):   # НОВОЕ, строго после
    return
if not _route_plain_text(chat_id, text, msg):
    handle(chat_id, text)
```
`_prompt_intake_intercept(chat_id, text) -> bool`: если `prompt_intake.consume`
вернул результат — маршрутизирует по `kind` (T3/T4) и возвращает True; иначе
False.

**Тесты (`tests/test_prompt_intake_router.py`):**
- **Порядок-зуб (спай):** batch-FSM ждёт текст И prompt_intake armed → скормить
  текст → `_swapbatch_text_intercept` вызван, `prompt_intake.consume` **НЕ**
  вызван (спай). **Мутация:** переставить хуки местами → тест падает (двойной
  консьюм).
- Только prompt_intake armed (batch не ждёт) → `consume` вызван, роутер вернул
  до `_route_plain_text` (спай на `_route_plain_text` не вызван).
- Никто не ждёт → оба интерсепта вернули False, ушли в `_route_plain_text`.

### T3 — `/animate`: кнопка «✍️ Свой промт» + чтение `pend["motion"]`
**Кнопка:** добавить в клавиатуру движка одиночного `/animate` строку
`{"text": "✍️ Свой промт", "callback_data": "anim:custom"}` (префикс `anim:`
уже в `FRIEND_ALLOWED_CALLBACK_PREFIXES`, доступ друга не ломается). Рисуем
только в standalone-ветке (`_animate_photo_intercept`, после rewrite
`sbeng:→anim:`), не в batch `build_engine_keyboard`.

**Диспатч `anim:custom`:** `prompt_intake.arm(chat, "animate")` + подсказка
«✍️ Пришли одну строку — что должно двигаться».

**Роутинг consume (kind="animate"):** записать `_ANIMATE_PENDING[chat]["motion"] =
motion` (пустой — игнор), подтвердить «✍️ Промт задан» и **перерисовать**
клавиатуру движка с пометкой, что промт задан (label из
`prompt_intake.awaiting_kind`/флага в pending).

**Чтение при генерации:** `control.py:2051` — заменить `motion=""` на
`motion=pend.get("motion", "")`. (Заметка: `_ANIMATE_PENDING` попается в
`_animate_run_single` до сборки request — прокинуть `motion` из попнутого pend.)

**Тесты:**
- Клавиатура standalone `/animate` содержит кнопку `anim:custom`; batch
  `build_engine_keyboard` — **НЕ** содержит (мутация: если протекла в batch —
  падает).
- `anim:custom` → `prompt_intake.is_awaiting(chat)` True (спай на arm).
- consume(kind=animate) пишет `motion` в `_ANIMATE_PENDING`; пустой текст →
  motion не перезаписан. **Мутация:** пустой перезаписывает → падает.
- **Зуб генерации:** `build_single_animate_request` получает
  `motion=pend["motion"]` (спай на аргумент). **Мутация:** вернуть хардкод `""`
  → падает.

### T4 — `/videoref`: кнопка «✍️ Свой промт» (сосуществует с Grok, ручной = бесплатный)
**Кнопка:** в сообщение после нарезки (`send_with_keyboard` на строке ~1531)
добавить вторую строку кнопок:
```python
[[{"text": f"🎬 Анализ движения (~${VIDEOREF_MOTION_USD:.2f})", "callback_data": "vref:motion"}],
 [{"text": "✍️ Свой промт (бесплатно)", "callback_data": "vref:custom"}]]
```
(`vref:` уже friend-allowed.)

**Диспатч `vref:custom` (тоггл со сбросом):**
- если `prompt_intake.is_awaiting(chat)` уже True → `disarm` + «✍️ Ручной промт
  отменён — можно нажать 🎬 Анализ движения» (возврат в Grok-режим);
- иначе → `prompt_intake.arm(chat, "videoref")` + подсказка.
- Кроме того, тап `vref:motion` (Grok) **тоже** делает `disarm` перед запуском —
  чтобы после переключения на Grok случайно набранный текст не был съеден как
  ручной промт. Обе кнопки остаются на экране (сосуществуют), так что «вернуться
  к Grok» = либо повторный тап «✍️ Свой промт», либо прямой тап «🎬 Анализ».

**Роутинг consume (kind="videoref"):** новый `_videoref_motion_manual(chat,
motion)` — **зеркало `_videoref_motion_run`, но без шагов 1/3/4**:
- НЕ вызывает `check_limit` (нечего гейтить — бесплатно),
- НЕ вызывает `generate_video_motion_prompt` (Grok),
- НЕ вызывает `record_cost`,
- делает только шаг 2 — `select_best_frame` (free); нет лица → «❌ не нашёл
  лицо»;
- сташит `_VIDEOREF_SWAP_PENDING[chat]` с `motion_prompt=motion` (+ те же
  `seconds`(snap реф-длины)/`smooth=False`/`engine_mode="spicy"`/
  `wardrobe_mode="preserve"`, что и платный путь),
- показывает то же duration-keyboard + текст, где **видно, что промт задан
  вручную** («✍️ Свой промт принят — выбери длину…»).

**Тесты:**
- Сообщение после нарезки содержит обе кнопки (`vref:motion` и `vref:custom`).
- `vref:custom` → `prompt_intake.is_awaiting` True.
- **Money-зуб (главный):** ручной путь — спай: `generate_video_motion_prompt`
  **НЕ** вызван, `record_cost` **НЕ** вызван, `check_limit` **НЕ** вызван; при
  этом `_VIDEOREF_SWAP_PENDING["motion_prompt"] == motion`. **Мутация в обе
  стороны:** если ручной путь зовёт Grok — падает; если платный путь перестал
  звать Grok — его тест падает (регрессия платного не проходит).
- Нет лица в кадрах → ошибка, pending НЕ выставлен.
- **Сброс-зуб:** повторный тап `vref:custom` при активном ожидании →
  `is_awaiting` снова False (disarm); тап `vref:motion` при активном ожидании
  intake → intake снят до Grok-запуска. **Мутация:** убрать disarm → падает
  (текст после переключения ушёл бы в ручной путь).

### T5 — тогглы качества/длины в одиночный `/animate` (порт `sbq:`)
**Паттерн из batch:** `build_quality_keyboard` (`sbq:dur:`/`sbq:res:`/`sbq:done`,
caps_for). Порт в standalone под новый префикс **`aq:`** (animate-quality —
мирроринг `anim:` vs `sbeng:`).

**Поток:** сейчас `anim:<engine>` → `_animate_run_single` бежит сразу на
дефолтах. Меняем: `anim:<engine>` (кроме `none`) → записать engine в
`_ANIMATE_PENDING`, показать quality-keyboard `caps_for(engine)`
(`aq:dur:<d>`/`aq:res:<r>`/`aq:done`). `aq:dur/res` → апдейт pending. `aq:done`
→ `_animate_run_single` читает `seconds`/`resolution` из pending (вместо
`caps.default_*`).

**Важно:** добавить `"aq:"` в `FRIEND_ALLOWED_CALLBACK_PREFIXES`
(`control.py:6747`) — иначе друг не сможет жать.

**Тесты:**
- quality-keyboard одиночного `/animate` строится по `caps_for(engine)`
  (длины/разрешения из caps выбранного движка).
- `aq:dur:10` / `aq:res:720p` пишут в `_ANIMATE_PENDING`.
- **Quote==charge-зуб:** limit-gate (`caps.cost_for`) и запуск читают
  `pend["seconds"]/pend["resolution"]`, а не дефолты. **Мутация:** вернуть
  дефолты → падает.
- `aq:` в friend-allowed префиксах (иначе тест доступа друга падает).
- Анти-дубль: pending попается в `_animate_run_single` (одно нажатие `aq:done`
  = одна генерация).

### T6 — эмодзи кнопки движка `🎬 → ⚙️` в `/videoref`
**Точка:** `control.py:1454` — `{"text": f"🎬 Движок: {caps.display_name}"…}` →
`⚙️`. Кнопки длительности (`_dur_btn`, строка 1438) остаются `🎬`/`⭐` — теперь
эмодзи движка и длины различимы.

**Тесты:**
- vref-keyboard: кнопка движка начинается с `⚙️`, кнопки длины — с `🎬`/`⭐`;
  эмодзи движка ≠ эмодзи длины. **Мутация:** вернуть `🎬` на движок → падает.

(Заметка: `build_engine_keyboard` engine-кнопки тоже используют `🎬`, но там нет
рядом длительностей — коллизии нет, **не трогаем**, вне области.)

---

## 6. Риски

- **R1 — двойное ожидание (batch + intake).** Митигация: жёсткий порядок
  хуков (T2) + порядок-зуб. Кнопки intake живут только в standalone/videoref
  потоках, отдельных от batch-FSM.
- **R2 — ручной videoref случайно спишет деньги.** Главный money-риск.
  Митигация: T4 money-зуб (спай, что Grok/record_cost/check_limit НЕ вызваны) +
  мутация в обе стороны.
- **R3 — реструктура `/animate` (T5) ломает анти-дубль / течёт pending.**
  Митигация: pop pending оставить в `_animate_run_single` (на `aq:done`);
  тест анти-дубля.
- **R4 — забыть `aq:` в `FRIEND_ALLOWED_CALLBACK_PREFIXES`** → друг не жмёт
  качество. Митигация: явный тест доступа (T5).
- **R5 — утечка seam-состояния между чатами.** Митигация: dict по `chat_id`,
  `consume` снимает ожидание; тест изоляции (T1).
- **R6 — cap читается не там/не тогда.** Cap берём из env в `consume`; граничный
  тест (T1).

---

## 7. Money-safety (сводка)

- Seam — чистый текст, ноль сети/стоимости (T1 money-зуб).
- `/videoref` ручной путь — сознательно бесплатный, Grok не зовётся (T4 зуб).
- `/animate` quote==charge наследуется: gate и запуск читают выбранное
  качество из pending (T5 зуб). Существующие гейты (`check_limit`/`record_cost`)
  не переписываются.

---

## 8. СТОП

Спека готова. **Жду ОК** перед кодом (дисциплина: спека → ОК → TDD). После ОК —
идём T1→T6 по TDD, бот в приоритете, existing целы.
