# CHATTER-2 — Telethon-юзербот (арка 2 из 3)

**Дата:** 2026-07-17
**Статус:** принят к реализации
**Предыдущее:** арка 1 (ядро) закрыта, HEAD `65f5780`, 162 теста, запушено. Ядро = `chatter/` (brain/humanizer/conversation/disclosure/guardrails/storage) + `Transport` ABC + fake-транспорт.
**Эта арка:** реальный транспорт Telegram через Telethon (MTProto, TAMAPI-стиль юзербот). Арка 3 = эскалация.

---

## 0. ГЛАВНЫЙ ИНВАРИАНТ — ПРОВЕРКА ШВА

Ядро НЕ трогать: `brain.py`, `humanizer.py`, `conversation.py`, `disclosure.py`, `guardrails.py` — **ни одной правки**. Telethon подключается ЗА интерфейсом `Transport` ABC + `Deps`. Если для интеграции пришлось лезть в один из этих файлов — шов плохой, ДОЛОЖИТЬ (не чинить молча).

**Async-мост (ключевое решение):** ядро синхронное (`process_batch`, инжектнутый `sleep`/`Transport`). Telethon асинхронный. Мост целиком в транспорте/раннере: `process_batch` крутится в worker-потоке (`asyncio.to_thread`), а методы транспорта `send`/`send_typing` маршалят корутины на loop (`run_coroutine_threadsafe`). `deps.sleep = time.sleep` (в worker-потоке — loop не блокирует). Ядро остаётся синхронным и нетронутым. Разрешённые правки вне «ядра»: `config/loader.py` (опциональный `telegram`-блок), `storage/db.py` НЕ трогаем (для /switch используем составной ключ контакта). Отчёт по арке ОБЯЗАН включать `git diff --stat` доказывающий 0 изменений в 5  core-файлах.

## 1. Файлы

```
chatter/
  requirements.txt            # + telethon
  transport/telethon_tg.py    # TelethonTransport(Transport ABC) + async-мост send/send_typing
  telethon_run.py             # async раннер: события→фильтр→debounce→process_batch(в потоке); /switch; защита аккаунта; алерты
  telethon_login.py           # разовый интерактивный логин (юзер запускает через ! )
.secrets/                     # сессия вне git (см. .gitignore)
tests/chatter/                # Telethon МОКАЕМ, реальный клиент в тестах НЕ поднимать
```
Корневой `.gitignore`: добавить `/.secrets/` (ОБЯЗАТЕЛЬНО — session = полный доступ к аккаунту, утечка страшнее ключа).

## 2. Авторизация (реквизиты + сессия)

- `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` из `.env` (env-переменные; login/раннер читают из окружения).
- Сессия: файл `.secrets/chatter_telethon.session` (путь настраиваемый env `TELETHON_SESSION`, дефолт в `.secrets/`). **Вне git.**
- **Логин = отдельный скрипт `chatter/telethon_login.py`**, юзер запускает `! python -m chatter.telethon_login`: спросит номер → код придёт в TG → 2FA-пароль если включён → сохранит сессию. Ассистент код/пароль НЕ видит (юзер вводит в своём терминале).
- Потеря сессии/логаут в рантайме (`AuthKeyError`/`Unauthorized`) → АЛЕРТ + graceful stop, НЕ молчаливая смерть.

## 3. Слушаем ТОЛЬКО личку

`events.NewMessage(incoming=True)`. Обрабатывать ТОЛЬКО если: `event.is_private` И НЕ `event.out` (не своё исходящее) И отправитель не бот (`sender.bot`) И не сервисное сообщение. Игнор: группы, каналы, свои исходящие, сервисные, боты — молча (лог debug).

## 4. НИКОГДА НЕ ПИШЕТ ПЕРВЫМ (хардкод, не конфиг)

Единственный путь к `send` — ответ на обработанное входящее от allowlisted-контакта. Нет проактивной отправки в диалоги. Исключение — операционные АЛЕРТЫ (см. §8), это отдельный путь в Saved Messages, не диалог. Банят холодную рассылку, не ответы — это защита аккаунта. Тест: без входящего события `send` не вызывается никогда.

## 5. ALLOWLIST (демо-фаза)

Отвечаем ТОЛЬКО id из `settings.telegram.allowlist` (стартовый: `237616472` + партнёры). Остальным — МОЛЧИМ полностью (не отвечаем, не храним диалог; лог info «ignored non-allowlisted <id>»). Случайный человек не должен получить бота, которого не заказывал. Allowlist берётся из ПЕРВОЙ (стартовой) персоны запуска, общий для demo/demo2.

## 6. Тайминги/typing/разбиение/коалесцирование — из хуманайзера, НЕ дублировать

- Typing-индикатор: `client.action(chat, 'typing')` (Telethon), проброшен через `TelethonTransport.send_typing`.
- Задержки/разбиение/ночной режим: `compose_reply` action-план как есть (интерпретируется в `process_batch`).
- Коалесцирование входящих: per-chat накопление в раннере (транспорт-сайд, разрешено спекой §4) с ПЕРЕИСПОЛЬЗОВАНИЕМ чистых `humanizer.debounce_ready(first,last,now,window,max_window)` и `coalesce` — решение чистое, механизм накопления async. Потолок `debounce_max` соблюдать.

## 7. /switch (demo-only, allowlist)

Allowlisted-контакт пишет `/switch` → переключить его персону demo↔demo2 + сбросить его диалог. Реализация БЕЗ правки storage: ключ контакта = `f"{tg_id}:{persona}"` — переключение персоны = новый ключ = свежая история автоматически. Раннер держит обе персоны (demo, demo2) загруженными + in-memory `{tg_id: persona}` (дефолт — стартовая). Партнёр в ОДНОЙ личке: Аня 23 (ru) ↔ Дмитрий 42 (en). Это демо-номер. Не-allowlisted `/switch` игнор.

## 8. Защита аккаунта

- **FloodWait** (`FloodWaitError`): не долбить — пауза на `e.seconds` (с экспоненциальным ростом при повторах, кап), АЛЕРТ, не ретраить агрессивно.
- **Дневной кап** из settings (`limits.daily_cap`) применяется и здесь (через `guardrails.within_daily_cap` поверх общего Store).
- **Алерты** → Saved Messages бот-аккаунта (`client.send_message('me', ...)`) + ВСЕГДА лог в stderr/файл. Триггеры: FloodWait, потеря сессии, необработанная ошибка отправки.
- Потеря сессии → алерт + graceful stop.

## 9. EN-детектор is_bot_question — УЖЕ СДЕЛАН в арке 1 (`65f5780`), не переделывать.

## 10. Тестирование + приёмка

- TDD. Telethon МОКАЕМ (unittest.mock: `TelegramClient`, события, `FloodWaitError`). Реальный клиент/сеть в тестах НЕ поднимать. Все юнит-тесты офлайн, ноль сети/трат (LLM = FakeLLM как в ядре).
- Живой прогон (после логина юзером): юзер пишет с основного аккаунта (237616472) → Аня отвечает в личке БЕЗ бейджа «бот». Отчёт + транскрипт. Проверить /switch вживую.

## 11. Checkpoints

- (E) транспорт + раннер: фильтрация/allowlist/never-first/debounce/process_batch-мост/switch/daily-cap — построены и покрыты мок-тестами; ШОВ доказан (0 изменений в 5 core-файлах).
- (F) защита аккаунта (FloodWait/session-loss/алерты) + login-скрипт + `.gitignore`/requirements + ЖИВОЙ логин (юзер) + живой прогон + транскрипт.

---

## 12. Живучесть (liveness pass, 2026-07-17) — по итогам первого живого прогона

Первый живой прогон Ани прошёл; всплыли «теллы» и один ПРОДОВЫЙ блокер. Правки:

- **✓✓ Галочки/read-receipt — ПОДТВЕРЖДЕНО ЖИВЬЁМ (fix #1).** `compose_reply` теперь эмитит `ReadAck`/`Online` в ритме `пауза чтения → online(on) → пометить прочитанным → печатает → …ответ… → печатает off → online(off)`. `Transport` получил `read_acknowledge()`/`set_online(on)` (Telethon: `send_read_acknowledge` + `account.updateStatus offline=not on`). В живом прогоне Аня читает сообщение (галочки обновляются) перед ответом — тэлл «отвечает не прочитав» закрыт.
- **Онлайн-статус (fix #2 тайминга-теллов):** аккаунт больше не «был давно», пока отвечает — `Online(True)` на время ритма, `Online(False)` в конце.
- **Ночной множитель (дизайн-баг):** `typing_duration` больше НЕ множится на `night_multiplier` (человек ночью не печатает медленнее, он позже ЗАМЕЧАЕТ). Множитель — только на паузу чтения/реакции. + потолок общего времени ответа `MAX_TOTAL_RESPONSE_SECONDS=90` (пропорциональное сжатие пауз). 150-символьный ночной ответ: было ~80с → стало ~36с.
- **Текст честности:** тон-строка и честный факт склеивались как «bio. honesty» (маленькая «я» после точки, два шаблона встык). Теперь одна фраза через связку `— честно говоря,` (per-language); ФАКТ честности не тронут, только формулировка.

- **🔴 CATCH-UP НА СТАРТЕ (ПРОДОВЫЙ БЛОКЕР).** Telethon без catch-up игнорирует всё, что пришло офлайн → любой рестарт/краш/деплой = молча потерянные лиды. Реализация: `telethon_run.select_missed()` (чистая, TDD) выбирает из непрочитанных личных диалогов входящие от allowlisted в пределах возрастного капа `CATCHUP_MAX_AGE_SECONDS=24ч`, хронологически; `TelethonRunner.catch_up_missed()` прогоняет их через тот же `process_batch` после коннекта (планируется в `run_client` через `on_connected` — выполняется ПОСЛЕ `client.start()`). Не отвечаем на прошлогоднее (возрастной кап).
- **Извинение за паузу (не хардкод):** если сообщение ждало >10 мин (`APOLOGY_AGE_THRESHOLD_SECONDS=600`), `process_batch(missed_age_seconds=…)` передаёт в мозг РАЗОВУЮ заметку `missed_reply_context()` — ИНСТРУКЦИЮ извиниться своими словами в тоне персоны, не шаблонную фразу.
- **⚠️ ШОВ (спека §0):** ради «извинения в промпт» СОЗНАТЕЛЬНО тронут один core-файл — `brain.py`: `Brain.reply(history, *, context_note=None)`. Обратно совместимо (дефолт None = прежнее поведение). Доложено, как требует §0 («если пришлось лезть — доложить, не чинить молча»). Остальные 4 core-файла (`humanizer`, `conversation`, `disclosure`, `guardrails`) правились в рамках liveness-задач по прямому запросу оператора, не ради Telethon-шва.
- **Живучесть раннера (fix #2):** раннер пишет heartbeat `state/chatter_heartbeat.txt` каждые 30с. Гардиан `JarvisChatterGuardian` (S4U scheduled task, `scripts/chatter_guardian_detached.ps1` + `register_chatter_guardian.ps1`) по образцу `JarvisBotGuardian`: single-instance PID-lock, liveness = процесс + свежий heartbeat, Stop-Old перед Start (лечит «6 инстансов дрались за сессию»), переживает необслуживаемый ребут. На DOWN → `scripts/chatter_watch_check.py` (stdlib-only, TDD) TG-алертит ОПЕРАТОРА (chat 237616472) через токен основного Jarvis-бота (не через лежащий userbot), с кулдауном.
