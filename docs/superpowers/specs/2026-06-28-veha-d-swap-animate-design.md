# Веха D — Свап лица + анимация на выходе /videoref

**Дата:** 2026-06-28
**Статус:** принято (обе развилки утверждены), реализация TDD D1–D6
**Контекст:** финал большой видео-фичи. Веха C готова и подтверждена живьём
(лучший кадр + промт движения). Веха D = «новый канал к старой линии»:
соединяем выход Вехи C с УЖЕ ГОТОВОЙ линией свап+анимация. Максимум
переиспользования 1:1, мост минимальный.

## Цель

После Вехи C у пользователя на руках: `best` (Path лучшего кадра) и
`result.prompt` (промт движения). Веха D даёт кнопку → просит фото-лицо →
свапает лицо на кадр → анимирует по промту движения (WaveSpeed spicy) →
отдаёт видео со своим лицом.

## Принятые развилки

1. **Источник лица — вариант 1 (изолированно).** Кнопка «🎭 Свап + анимация»
   после Вехи C → бот просит прислать фото-лицо → пользователь шлёт → запуск.
   Состояние живёт в videoref-модуле (module-level dict), НЕ в
   BatchOrchestrator-сессии. Вариант 2 (переиспуем /swapbatch_source) отклонён
   как хрупкий по состояниям; вариант 3 (новая команда) — лишняя команда.

2. **Движок — WaveSpeed spicy по умолчанию, без меню.** Вся videoref-арка про
   uncensored spicy. `engine_mode="spicy"` явно. Дефолты caps: 5с / 720p.

3. **Money — вариант 1: единый check_limit заранее на ПОЛНУЮ сумму.**
   `est = SWAP_USD ($0.02) + caps.cost_for(5,"720p") (~$0.27) ≈ $0.29`.
   Отказ лимита → 0 свапа, 0 аним, 0 record_cost, честный отказ другу + ноти
   админу. `record_cost` ПО СТАДИЯМ по факту успеха: свап ок → $0.02;
   аним ок → caps. Свап ок + аним упала → списан ТОЛЬКО $0.02.
   `quoted == charged`: тот же `est`, что в check_limit, считается из тех же
   констант, что списываются. Вариант 2 (свап без пред-гейта) отклонён —
   $0.02-свап пробил бы лимит друга (тот же класс бага, что двойной свап).

## Архитектура: переиспользование vs мост

### Переиспуем 1:1 (ноль нового кода)

| Кусок | Где |
|---|---|
| Свап одного кадра | `get_swap_engine().swap_batch(source, [best])` → `list[Path\|None]` (`block_m2_face_swap/engines/factory.py`) |
| Сборка animate-запроса | `handler.build_single_animate_request(chat_id, image_path=, motion=, engine_mode="spicy", seconds=, resolution=)` (`face_swap_handler.py:669`) |
| Анимация одного | `animate_batch(engine, [req], concurrency=1)` (`batch_animate.py`) |
| Выбор движка | `EngineRouter().select("spicy")` (`engines/router.py`) |
| Цены/капы | `caps_for("spicy").cost_for(seconds, resolution)` (`engines/capabilities.py`) |
| Money | `check_limit(uid, estimated_usd=)`, `_cost.record_cost(uid, uname, amt)` |
| Telegram | `_send_local_photo`, `_send_local_video`, `_download_telegram_file`, `send`, `send_with_keyboard`, `answer_callback_query` |
| Friend-доступ | `vref:` уже в `FRIEND_ALLOWED_CALLBACK_PREFIXES` (`:6170`) — `vref:swapanim` покрыт автоматически |
| Паттерн single-флоу | `_animate_run_single` (`:1476`) — копируем структуру воркера, lock, биллинг |

### Мост (новое, минимум) — всё в `jarvis_smart_telegram_control.py`

| # | Новое | Что |
|---|---|---|
| 1 | `_VIDEOREF_SWAP_PENDING: Dict[int, Dict]` | стэш `{best_frame: Path, motion_prompt: str}` после Вехи C |
| 2 | `_VIDEOREF_FACE_AWAITING: set` | строгий флаг «ждём фото-лицо» для изоляции перехвата |
| 3 | константы | `VIDEOREF_SWAP_USD = 0.02` (или reuse `SWAPBATCH_SWAP_USD_PER_PHOTO`); est берётся из них же |
| 4 | правка хвоста `_videoref_motion_run` (`:1421-1422`) | заглушка → стэш + кнопка «🎭 Свап + анимация (~$est)» (callback `vref:swapanim`) |
| 5 | кейс `vref:swapanim` в callback-dispatch (`:3176`) | армит face-awaiting + просит фото; нет pending → мягко |
| 6 | `_videoref_face_intercept(chat_id, msg)` | зеркало `_videoref_intercept`, но для photo; СТРОГО по `_VIDEOREF_FACE_AWAITING` |
| 7 | wiring `_videoref_face_intercept` в photo-цепочку (`:7028`) | ПЕРЕД `_animate_photo_intercept`/`_swapbatch_photo_intercept` |
| 8 | `_videoref_swapanim_run(chat_id, source_face)` | оркестрация: money-гейт → свап → аним → видео |

## UX-поток

```
/videoref → видео → нарезка (B) → "🎬 Анализ движения ~$0.35" (C)
  → [кадр выбран + промт] фото с caption (C готова)
  → 🆕 "🎭 Свап + анимация (~$0.29)"            ← Веха D
  → клик → бот: "Пришли фото-лицо для свапа"
  → Daniil шлёт фото-лицо
  → _videoref_swapanim_run:
      check_limit($0.29) → отказ? стоп, 0 списано, ноти админу
      swap_batch(лицо, [кадр]) → swapped; успех → record $0.02; фейл → 0, стоп
      build_single_animate_request(swapped, motion=промт, "spicy",5с,720p)
        + animate_batch(engine,[req],1) → видео; успех → record caps; фейл → 0
      → 🎥 видео со своим лицом
```

## Money-инвариант (D4/D5 — с зубами)

- check_limit на полную сумму ДО любой траты или скачивания/свапа.
- Порядок строгий: gate → свап → (record свап) → аним → (record аним).
- record_cost НИКОГДА до успеха своей стадии. Аним фейл после свапа = только $0.02.
- est для gate == сумма констант, которые списываются (quoted == charged).
- Spy-тесты ломают инвариант (свап до gate / record до успеха) и должны падать.

## Изоляция (D3 — тест)

- `_videoref_face_intercept` возвращает True ТОЛЬКО если
  `chat_id in _VIDEOREF_FACE_AWAITING` И есть `msg.get("photo")`.
- Не армлено → False → фото падает дальше в /animate и swapbatch (целы).
- Флаг снимается при захвате сообщения (как `_videoref_intercept` disarm).

## Анти-дубль (D6)

- pending консумится `pop` при клике `vref:swapanim` (как `vref:motion`).
- Второй клик находит пусто → мягкое сообщение, 0 трат.

## Не сломать Веху C

- Меняем ТОЛЬКО хвост `_videoref_motion_run` (`:1421-1422`: заглушка →
  стэш + кнопка). Money-гейт C ($0.35), best-frame, Grok-вызов, refusal-ветки —
  не трогаем.

## TDD-задачи (коммит + СТОП после каждой)

- **D1 — Handoff:** стэш `{best, motion_prompt}` + кнопка вместо заглушки.
  Tests: после success-анализа pending сохранён + кнопка `vref:swapanim` показана.
- **D2 — Кнопка → запрос лица:** callback `vref:swapanim` армит face-awaiting +
  просит фото; нет pending → мягко. Tests.
- **D3 — Перехват лица (изоляция):** `_videoref_face_intercept` строго по флагу;
  армлено+фото → True + воркер; не армлено → False (чужие фото целы). Tests.
- **D4 — Money с зубами:** единый check_limit на полную сумму ДО трат; отказ →
  0 свапа/аним/record + ноти. Spy: свап-до-gate / record-до-успеха → падение.
- **D5 — Мост свап+аним:** swap_batch-of-1 → record $0.02 при успехе;
  build_single_animate_request + animate_batch (spicy,5с,720p) → record caps;
  per-stage (свап ок + аним фейл = только $0.02); видео отправлено. Моки движков.
- **D6 — Анти-дубль:** второй клик → пусто, 0 трат. Tests.

⚠️ После кода — **PENDING живой платный прогон** (рестарт бота + реальный
свап+аним со своим лицом на WaveSpeed spicy).
