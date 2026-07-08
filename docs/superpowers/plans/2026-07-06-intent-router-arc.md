# Intent-Router Arc (IR) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **STATUS: SPEC — ждёт ОК Артёма/Daniil перед кодом** (правило «спека→ОК→код»). Разведка — фактом, ниже с `file:line`. Кода в этой сессии НЕ писалось. Неоднозначности — в разделе «Допущения».

**Goal:** Admin (а на разрешённых командах — и friend) пишет боту обычной фразой («глянь что с ботом», «сделай фото блюда», «что я потратил сегодня») → Jarvis распознаёт интент → маппит на команду из реестра меню (110 команд) → бесплатную/безпараметровую выполняет сразу, платную/с параметрами предлагает кнопкой «Запустить /X?», непонятную — честно отбивает «не понял, вот /menu» (НЕ угадывает).

**Architecture:** Новый чистый модуль `tools/intent_router.py` строит матч-корпус из готового реестра `tools/jarvis_menu.py` и матчит фразу каскадом: **(IR-1)** бесплатный офлайн-слой (keyword + `difflib`-fuzzy, $0, синхронный, чистый) → при неуверенности **(IR-2)** платный Haiku-классификатор (~$0.002, только admin, через ledger). Роутер встаёт ПОСЛЕ всех существующих перехватов, в самом хвосте `classify_message` (перед catch-all `chat`), поэтому существующие текст-флоу не меняются. Исполнение и подтверждение идут через уже существующий `handle()` — переиспользуются все три «зуба изоляции» и money-гейт.

**Tech Stack:** Python 3.14, stdlib `difflib` (fuzzy — БЕЗ новых зависимостей; rapidfuzz/Levenshtein в venv НЕТ), `anthropic` SDK для IR-2 (Haiku, паттерн из `app/services/quick_answer.py`), Telegram inline-кнопки (`send_with_keyboard` + `handle_callback_query`), pytest + мутационные «зубы».

---

## 0. Разведка фактом (ответы на 5 вопросов брифа)

Всё проверено чтением кода. `bot.py` = `tools/jarvis_smart_telegram_control.py`.

### 0.1 Что уже есть: keyword-матчинг (вопрос 1)

Уже существует **полноценный keyword-каскад** `classify_message(text, state)` — `bot.py:3308`, финиширующий в `run_intent(...)` — `bot.py:3871`.

- «здоровье системы» → `{"intent":"health"}` — `bot.py:3399` (`if any(x in t for x in ["статус","здоровье системы","системы работают","проверить системы"])`).
- Каскад покрывает ~20 хардкод-интентов: `greeting`, `small_talk`, `continuation`, `capabilities`, `can_you`, файловые, `table`, `generate`, `research`, `engineer`, `brain`, `identity`, `compound_task`, … и завершается **catch-all `{"intent":"chat"}`** — `bot.py:3551`.
- `detect_command` — `bot.py:2979` — первым делом ловит `/slash` внутри `classify_message` (`bot.py:3312`), так что слэш-команды роутер не трогает.
- **Важно про catch-all `chat`** (`run_intent`, `bot.py:4170-4183`): текущее поведение на непонятный текст — **угадать через ПЛАТНЫЙ Perplexity-research БЕЗ money-гейта** (`send("💬 Понял. Дам ответ через интернет/brain, чтобы не гадать.")` → `backend_post("/api/jarvis/tools/internet/research", …)`). Это ровно то «угадывание», что бриф просит убрать; заодно это незакрытая money-дыра (`/research`-пути без `check_limit` — см. 0.4).

**Вывод: РАСШИРЯТЬ, не заменять.** Существующий каскад оставляем как есть (инвариант «не сломать флоу»); новый menu-роутер вставляем **новой ступенью в хвосте** `classify_message` — он видит только текст, не пойманный ни одним существующим триггером. Замена касается ровно одного места: catch-all `chat` → (IR-каскад → честный фолбэк).

### 0.2 Датасет: реестр меню (вопрос 2а)

`tools/jarvis_menu.py`: `MENU` — `list` из 11 категорий (`jarvis_menu.py:233`), **110 пунктов**. Пункт — frozen `MenuItem(cmd, label, action, friend, hint)` (`jarvis_menu.py:28-34`):
- `cmd` — каноническая команда со слешем (`"/health"`).
- `label` — русская подпись 3-5 слов **или `None`** (у категорий apps/agents/stats/system — голая команда).
- `action` — `"exec"` (сразу `handle()`) | `"hint"` (показать подсказку). **Это про параметры/шаги, НЕ про деньги.**
- `friend` — видимость роли friend.
- `hint` — текст подсказки.
- `lookup_item(cmd, role)` — role-checked (`jarvis_menu.py:268-278`).

Готовые (cmd, label) для матчинга (verbatim): `/videoref`—«референс-видео → свап+анимация», `/menu_photo`—«фото блюда для меню», `/animate`—«анимировать одно фото», `/health`—«здоровье Jarvis», `/git_status`—«статус git», `/me_as`—«я в роли <роль>», `/smart_photo`—«умное фото по описанию».

**Добор для `label=None`:** `NATIVE_ADMIN_COMMANDS` (`jarvis_menu.py:285-307`) и `NATIVE_FRIEND_COMMANDS` (`309-318`) — второй датасет (name, русское описание) для `setMyCommands`, напр. `("browse_check","Проверить страницу (браузер)")`. Покрывает ~21 admin / ~8 friend топ-команд, включая безлейбловые. Итоговый корпус команды = `label` + native-описание + токены `cmd` (без слеша, split `_`) + рукописные алиасы (см. 2.1).

**Критично: в реестре НЕТ поля цены/paid/free.** paid/free придётся держать отдельным source-of-truth (см. 4.1).

### 0.3 Каскад распознавания (вопрос 2)

- **(а) Дешёвый слой IR-1** — keyword + fuzzy по корпусу из 2.1. Библиотек fuzzy в venv нет → stdlib `difflib.SequenceMatcher` + token-overlap. $0, чисто, синхронно. Живёт в `classify_message` (чистая функция, юнит-тесты без сети).
- **(б) LLM-слой IR-2** — Haiku. Инфра готова: `app/services/quick_answer.py:58` вызывает `client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=300, system=…, messages=[…])` (`quick_answer.py:95-100`); анти-галлюцинационный system-промт («NEVER pretend you executed an action»). Роутер (`JARVIS_ROUTER_ENABLED`) — Sonnet, opt-in, default-off (`bot.py:8273`), нам не подходит (дорого/тяжело). Делаем **отдельный дешёвый classifier-хелпер** (возвращает `cmd | none`), модель `claude-haiku-4-5-20251001`. IR-2 сетевой/платный → живёт в исполнительном слое (`run_intent`), НЕ в `classify_message`.
- **Каскад:** IR-1 first (бесплатно). HIGH → роут; AMBIGUOUS → кнопки-уточнение; UNCERTAIN → IR-2 (только admin); NONE → честный фолбэк. **IR-2 никогда не вызывается, если IR-1 уверен** (экономия).

### 0.4 Порядок текст-роутера + инвариант (вопрос 3)

Полная карта дипатча входящего ТЕКСТА (от `process_update`, `bot.py:8495`):

| Слой | Перехват | Строка | Эффект |
|---|---|---|---|
| pre | `_whitelist_gate` | 8497 | дроп чужих |
| pre | `_cost_command_intercept` (/my_stats,/admin_costs) | 8501 | ест до classify |
| pre | `_admin_command_intercept` (/admin_*) | 8503 | ест до classify |
| pre | callback queries | 8508-8524 | не текст |
| pre | `_member` флаг | 8529 | membership |
| text | `_swapbatch_text_intercept` (FSM custom-prompts) | 8581 | `return` если ловит |
| text | `_prompt_intake_intercept` (motion для /animate,/videoref) | 8586 | `return` если ловит |
| text | `_route_plain_text`→`_run_router` (LLM-роутер, default-OFF) | 8588 | `return` если включён и ответил |
| — | **`handle(chat_id, text)`** | 7516 | основной путь |
| in-handle | role-гейт `_role_for_chat` | 7517 | admin/friend/deny |
| in-handle | **friend-allowlist гейт (по 1-му токену)** | 7524-7529 | friend + токен∉`FRIEND_ALLOWED_COMMANDS` → «🚫» |
| in-handle | `classify_message` | 7533 | классификация |
| in-handle | intent==command → `handle_command` | 7536-7537 | слэш-команды |
| in-handle | LoRA text-state | 7550-7555 | `return` если ловит |
| in-handle | Persona Creator dialog | 7570 | `return` если ловит |
| in-handle | Landing Brief session | 7577 | `return` если ловит |
| in-handle | Smart Photo Router | 7586 | `return` если ловит |
| in-handle | **`run_intent`** | 7591 | исполнение интента |

**Куда встаёт новый роутер:** IR-1 — новая ступень в самом конце `classify_message`, **перед** `return {"intent":"chat"}` (`bot.py:3551`). Все FSM-перехваты (swapbatch/prompt_intake/LoRA/persona/landing/photo) `return`-ят раньше `run_intent`, а `detect_command` ловит слэши первым — значит роутер физически видит **только остаточный текст, не пойманный ничем**. **Инвариант: меняется ровно один исход — бывший `chat`; все прочие интенты и FSM нетронуты.** Тест-зуб: прогон существующих classify-тестов зелёный + свежие «эти фразы всё ещё дают старый интент, не ir_*».

### 0.5 Безопасность (вопрос 4)

- **Money.** Централизованного реестра цен НЕТ; `guard_spend(user_id, username, estimated_usd, do_spend)` (`app/services/auth/spend_guard.py:21`) = check_limit-до-траты. Единственный существующий **explicit confirm-гейт** — `/dev_task` (`devtask:confirm`, `bot.py:4374`); генерация фото/видео резервирует-и-бежит без диалога. admin — безлимит-но-с-записью (`access_control.py:21`), friend — cap-гейт, ledger — только видимость (не блокирует). Де-факто прайс разбросан по хендлерам (`VIDEOREF_MOTION_USD $0.35 bot.py:1791`, `PERSONA_VIDEO_USD $0.40 bot.py:456`, `PHOTO_DISH_USD $0.04`, …). → **Роутер вводит собственный source-of-truth `FREE_AUTOEXEC` (белый список) и `PAID` (для гейта/подписи).** Правило: **авто-exec ТОЛЬКО если `cmd ∈ FREE_AUTOEXEC`; всё прочее → confirm-кнопка.** Инвариант «money НИКОГДА не exec сразу» гарантируется структурно тестом `FREE_AUTOEXEC ∩ PAID == ∅`.
- **Friend.** Гейт `bot.py:7524-7529` сейчас блокирует любой свободный текст friend (первый токен фразы ∉ `FRIEND_ALLOWED_COMMANDS` — а там слэш-команды). `FRIEND_ALLOWED_COMMANDS` — frozenset ~55 команд (`bot.py:7661`). Callback-гейт: `FRIEND_ALLOWED_CALLBACK_PREFIXES` (`bot.py:7689`). → Роутер для friend: **отдельный узкий путь** (см. 3.2) — свободный текст friend матчится ТОЛЬКО против `FRIEND_ALLOWED_COMMANDS ∩ friend-visible`, не проваливается в общий каскад (иначе friend получил бы research/generate — расширение прав). IR-2 (Haiku) для friend **выключен** (не тратим их cap на классификацию). Непонятное → «🚫/вот меню».
- **Опечатки/двусмысленность.** AMBIGUOUS (топ-2 в узком зазоре) → кнопки-уточнение, не угадывание. Ниже порога → честный фолбэк.

### 0.6 Confirm-паттерн для переиспользования

`send_with_keyboard(chat_id, text, inline_keyboard) -> message_id` (`bot.py:259`); диспетчер `handle_callback_query` (`bot.py:4304`) роутит по `data.startswith("<prefix>:")`, формат `prefix:action[:arg]`. Образец — `/dev_task`: кнопка `devtask:confirm:<tid>` (`bot.py:1333`) → ветка `bot.py:4374` → `_devtask_confirm`. Menu-exec: `menu:x:<cmd>` → `handle(chat_id, item.cmd)` (`bot.py:4358`, зуб #2). **Новый префикс `ir:`** + добавить в `FRIEND_ALLOWED_CALLBACK_PREFIXES`. **callback_data ≤ 64 байт** (кириллица = 2 б/симв) → параметры НЕ кладём в data; храним `state["pending_ir"]` (как `table_clarification`, `bot.py:3323`), data = `ir:run` / `ir:pick:<idx>` / `ir:cancel`.

---

## 1. Архитектура: модуль и модель данных

**Новый файл `tools/intent_router.py`** — чистый, НЕ импортирует `bot.py`, НЕ ходит в сеть в IR-1 (симметрия с `jarvis_menu.py`). Импортирует `tools.jarvis_menu` (реестр).

```python
# tools/intent_router.py  (сигнатуры-контракт; полная реализация — по задачам ниже)
from dataclasses import dataclass
from typing import Literal, Optional

Role = Literal["admin", "friend"]
Decision = Literal["route", "clarify", "uncertain", "none"]

@dataclass(frozen=True)
class Candidate:
    cmd: str          # "/menu_photo"
    score: float      # 0..1
    arg: str = ""     # извлечённый параметр (IR-3; в IR-1/2 пусто)

@dataclass(frozen=True)
class IRResult:
    decision: Decision
    candidates: tuple           # tuple[Candidate, ...], отсортированы по score убыв.
    reason: str = ""            # для лога/дебага

def resolve_offline(text: str, role: Role) -> IRResult: ...   # IR-1, $0, чисто
def auto_exec_ok(cmd: str) -> bool: ...                        # cmd in FREE_AUTOEXEC
def is_paid(cmd: str) -> bool: ...                             # cmd in PAID
```

Пороговая модель IR-1 (стартовые числа, тюнятся вживую):
- нормализация: lower, ё→е, убрать пунктуацию, схлопнуть пробелы.
- score(cmd) = max(alias-hit=1.0 при точном/подстрочном совпадении рукописного алиаса; token-overlap = |tok(query)∩tok(corpus)|/|tok(query)|; `difflib.SequenceMatcher.ratio` по лучшей фразе корпуса).
- решение: `best≥0.72 и (best-second)≥0.15` → **route**; `best≥0.72 и зазор<0.15` → **clarify** (топ 2-3); `0.45≤best<0.72` → **uncertain** (admin→IR-2; friend→clarify топ-2); `best<0.45` → **none**.

## 2. Построение датасета

### 2.1 Корпус матчинга
Функция `build_corpus()` (в `intent_router.py`, строится один раз, кешируется на модуле):
- источники на команду: `MenuItem.label` (если есть) + native-описание из `NATIVE_ADMIN_COMMANDS`/`NATIVE_FRIEND_COMMANDS` + токены `cmd.lstrip('/').split('_')` + рукописный `ALIASES[cmd]`.
- `ALIASES: dict[str, tuple[str,...]]` — рукописные фразы для топ-команд (главный рычаг точности). Стартовый набор (пример):
  - `/health`: («что с ботом», «глянь бота», «бот жив», «здоровье», «статус систем»)
  - `/git_status`: («что с гитом», «статус кода», «незакоммиченное»)
  - `/costs`, `/my_stats`: («что я потратил», «сколько потратил», «расходы», «траты сегодня»)
  - `/menu_photo`: («фото блюда», «сфоткай блюдо», «фото для меню»)
  - `/regress`: («прогони тесты», «регресс»)
  - `/logs_tail`: («покажи логи», «хвост логов»)
- корпус — только команды, существующие в `MENU` (никаких фантомов; тест-зуб: каждый ключ `ALIASES` и `FREE_AUTOEXEC`/`PAID` есть в реестре).

### 2.2 Роле-фильтр датасета
`resolve_offline(text, "friend")` матчит ТОЛЬКО против `{cmd | cmd∈FRIEND_ALLOWED_COMMANDS}` (source-of-truth прав — frozenset из `bot.py`, прокидывается параметром/константой, чтобы модуль не импортировал `bot.py`; см. Задачу 4).

## 3. Точки встраивания (wiring)

### 3.1 Admin-путь — хвост `classify_message` (`bot.py:3551`)
Заменить `return {"intent": "chat", "query": raw}` на:
```python
    ir = intent_router.resolve_offline(raw, "admin")
    if ir.decision == "route":
        c = ir.candidates[0]
        return {"intent": "ir_route", "command": c.cmd, "arg": c.arg}
    if ir.decision == "clarify":
        return {"intent": "ir_clarify", "candidates": [c.cmd for c in ir.candidates[:3]]}
    if ir.decision == "uncertain":
        return {"intent": "ir_uncertain", "query": raw}   # → IR-2 (admin)
    return {"intent": "ir_unknown", "query": raw}          # честный фолбэк (заменяет chat-guess)
```
(Старый `chat`→research больше не достигается из основного текст-пути; см. Задачу 7 — money-дыра закрывается.)

### 3.2 Friend-путь — узкий, в `handle()` (`bot.py:7524-7529`)
Расширить friend-гейт: если `role=="friend"` и текст НЕ начинается с `/` (свободная фраза) — вместо мгновенного «🚫» вызвать `intent_router.resolve_offline(text, "friend")` и:
- `route`/`clarify` → те же `run_intent`-ветки (ниже), команды гарантированно ∈ `FRIEND_ALLOWED_COMMANDS`;
- `uncertain`/`none` → «🤷 Не понял. Вот что я умею: /menu».
- friend НЕ проваливается в общий `classify_message`-каскад (права не расширяются). IR-2 для friend не зовётся.

### 3.3 Исполнение — `run_intent` (`bot.py:3871+`, новые ветки)
```python
    if intent == "ir_route":
        cmd = pack["command"]; arg = pack.get("arg", "")
        if not arg and intent_router.auto_exec_ok(cmd):
            handle(chat_id, cmd)                              # бесплатно+безпараметрово → сразу
        else:
            _ir_send_confirm(chat_id, cmd, arg, state)        # платно/с параметром → кнопка
        return
    if intent == "ir_clarify":
        _ir_send_clarify(chat_id, pack["candidates"], state)  # кнопки-уточнение
        return
    if intent == "ir_uncertain":                              # только admin достигает
        cmd = _ir_haiku_classify(chat_id, pack["query"])      # IR-2, платно, admin, recorded
        if cmd:
            _ir_send_confirm(chat_id, cmd, "", state)         # Haiku-догадка → всегда confirm
        else:
            _ir_unknown(chat_id)
        return
    if intent == "ir_unknown":
        _ir_unknown(chat_id)                                  # «не понял, вот /menu»
        return
```
`_ir_send_confirm` кладёт `state["pending_ir"] = {"cmd":…, "arg":…}` и шлёт `send_with_keyboard(…, [[{"text":"▶️ Запустить {cmd}", "callback_data":"ir:run"},{"text":"Отмена","callback_data":"ir:cancel"}]])`; для платных добавляет «(платно ~$X)» из прайса. `_ir_send_clarify` кладёт `candidates` в `state` и шлёт по кнопке `ir:pick:<idx>` на каждого.

### 3.4 Callback — `handle_callback_query` (`bot.py`, новая ветка + allowlist)
```python
    if data.startswith("ir:"):
        action = data.split(":")[1] if ":" in data else ""
        pend = state.get("pending_ir") or {}
        if action == "run":
            answer_callback_query(cq_id, "▶️"); state["pending_ir"]=None; save_state(state)
            cmd = pend.get("cmd",""); arg = pend.get("arg","")
            handle(str(chat_id), (cmd + " " + arg).strip())   # зуб #2 (+money-гейт downstream)
        elif action == "pick":
            idx = int(data.split(":")[2]); cands = pend.get("candidates") or []
            ...outbound handle(...) по выбранному...
        elif action == "cancel":
            answer_callback_query(cq_id, "Отменено"); state["pending_ir"]=None; save_state(state)
        return
```
Добавить `"ir:"` в `FRIEND_ALLOWED_CALLBACK_PREFIXES` (`bot.py:7689`), иначе friend не нажмёт свою же кнопку.

## 4. Безопасность — константы (source-of-truth)

В `tools/intent_router.py`:
- `FREE_AUTOEXEC: frozenset` — бесплатные, безпараметровые, read-only (стартовый консервативный набор): `/health, /git_status, /costs, /my_stats, /smart_health, /debug_health, /capabilities, /menu, /swapbatch_status, /list_loras, /lora_status, /me_roles, /me_places, /me_styles, /party_themes, /dish_styles, /help, /start, /logs_tail`. **`/regress` НЕ включаем** (тяжёлый ~5 мин → confirm).
- `PAID: frozenset` — из де-факто прайса (0.5): все `/me_*`, `/menu_photo, /social_post, /pro_food, /smart_photo, /party_promo, /invite_card, /event_photo, /menu_book, /faceswap, /enhance, /videoref, /animate, /animate_batch, /animate_batch_go, /create_persona, /train_lora, /persona_photo, /persona_video, /persona_batch, /dev_task, /research` (+ swapbatch-генерация).
- `PRICE: dict[str,float]` — подпись для confirm (переиспользует значения из хендлеров).
- **Инвариант-зуб:** `assert FREE_AUTOEXEC & PAID == frozenset()` (тест). Каждый элемент всех трёх ∈ `MENU`-cmd (кроме `/research`/`/logs_tail` — проверить наличие; если нет в MENU → в отдельный ALLOW-список без реестрового зуба).

---

## 5. Поэтапность

- **IR-1 (эта спека, ядро):** keyword+fuzzy по реестру, $0. Admin (полный каскад + хвост) и friend (узкий путь). Confirm-кнопки, честный фолбэк, замена chat-guess. Задачи 1-8.
- **IR-2 (эта спека):** Haiku-каскад для UNCERTAIN, только admin, через ledger. Задачи 9-12.
- **IR-3 (намётка, отдельная спека после IR-1/2 живьём):** интент с параметрами. «фото борща»→`/menu_photo борща`. Slot-extraction: фраза минус матч-токены = аргумент; всегда confirm с показом извлечённого параметра («Запустить /menu_photo борща?»). Параметр — в `state["pending_ir"]["arg"]` (не в callback_data). Риски: разбор кириллицы, конфликт с существующим triggerом `generate` (напр. «сделай фото …» уже может ловиться intentом `generate`, `bot.py:3493`) — решается приоритетом триггеров/аудитом, отдельной аркой.

---

## 6. Задачи IR-1 (TDD, bite-sized)

### Task 1: Скелет модуля + корпус
**Files:** Create `tools/intent_router.py`; Test `tests/test_intent_router.py`.
- [ ] Тест: `build_corpus()` возвращает запись для каждой из 110 команд `MENU`; у `/health` в корпусе есть токен «здоровье»; у безлейбловой (`/my_stats`) — токен из native-описания или из cmd.
- [ ] Запуск: FAIL (нет модуля).
- [ ] Реализация: `build_corpus()` из `tools.jarvis_menu.MENU` + `NATIVE_*` + `ALIASES`.
- [ ] Запуск: PASS. Commit.

### Task 2: Скорер + решение
**Files:** Modify `tools/intent_router.py`; Test `tests/test_intent_router.py`.
- [ ] Тесты (фикстуры-фразы): «глянь что с ботом»→route `/health`; «что я потратил сегодня»→route `/costs`|`/my_stats`; «фото блюда для меню»→route `/menu_photo`; двусмысленная короткая→clarify (≥2 кандидата); «асдфгхй»→none; «расскажи про квантовую физику»→none/uncertain (НЕ команда).
- [ ] FAIL → реализация `resolve_offline` (нормализация, score, пороги 1.0) → PASS. Commit.

### Task 3: FREE_AUTOEXEC / PAID / инвариант
- [ ] Тест-зуб: `FREE_AUTOEXEC & PAID == frozenset()`; `auto_exec_ok("/health") is True`; `auto_exec_ok("/menu_photo") is False`; `is_paid("/videoref") is True`; каждый элемент множеств валиден против реестра.
- [ ] FAIL → добавить константы + `auto_exec_ok`/`is_paid` → PASS. Commit.

### Task 4: Роле-фильтр friend
**Files:** Modify `tools/intent_router.py` (принимает `friend_allowed: frozenset` параметром/инъекцией, НЕ импортит bot.py); Test.
- [ ] Тест: `resolve_offline("создать сайт","friend")`→none; фраза на friend-разрешённую команду→route; фраза на admin-only команду (`/git_status`) под ролью friend→none (не утекает).
- [ ] FAIL → фильтр кандидатов по роли → PASS. Commit.

### Task 5: Wiring в classify_message (admin-хвост)
**Files:** Modify `bot.py:3551`; Test `tests/test_intent_router_wiring.py`.
- [ ] Тест: `classify_message("глянь что с ботом", {})` (admin-контекст)→`{"intent":"ir_route","command":"/health",...}`; существующие интенты (health-триггер «здоровье системы», table, generate) НЕ регрессируют (перечислить 5 фраз-сентинелов, всё ещё дают старый интент).
- [ ] FAIL → вставка ступени перед chat-catch-all → PASS. Commit.

### Task 6: run_intent — ветки ir_route/ir_clarify/ir_unknown + confirm
**Files:** Modify `bot.py` (`run_intent`, новые `_ir_send_confirm/_ir_send_clarify/_ir_unknown`); Test (spy на `send`/`send_with_keyboard`/`handle`).
- [ ] Тест: `ir_route` + `/health` (∈FREE_AUTOEXEC, без arg)→зовётся `handle(chat,"/health")`, кнопок нет; `ir_route`+`/menu_photo` (PAID)→НЕ зовётся handle, шлётся `send_with_keyboard` с `callback_data "ir:run"` и текстом с «$»; `ir_unknown`→`send` с «/menu», research НЕ зовётся.
- [ ] FAIL → реализация → PASS. Commit.

### Task 7: Замена chat-guess честным фолбэком (money-safety)
**Files:** Modify `bot.py:4170-4185` (ветка `chat`); Test.
- [ ] Тест: непонятный admin-текст, не пойманный каскадом, НЕ вызывает `backend_post(".../internet/research", …)` (spy: 0 вызовов), а даёт honest-фолбэк. (Ветка `chat` теперь недостижима из основного пути — либо удалить, либо оставить как явный dead-safe.)
- [ ] FAIL → перенаправить на `_ir_unknown` → PASS. Commit.

### Task 8: Callback `ir:` + friend-узкий-путь + зубы
**Files:** Modify `bot.py` (`handle_callback_query` +ветка `ir:`; `FRIEND_ALLOWED_CALLBACK_PREFIXES` +`"ir:"`; friend-гейт `7524-7529`); Test.
- [ ] Тест (зубы обе стороны): `ir:run` из `pending_ir`→`handle(chat, "/cmd")`; `ir:cancel`→очищает `pending_ir`, handle не зовётся; friend жмёт `ir:run`→проходит prefix-гейт; friend свободный текст на разрешённую→route, на admin-only→«🚫/menu» (не утечка); admin свободный текст→полный каскад.
- [ ] FAIL → реализация → PASS. Commit.

## 7. Задачи IR-2 (TDD)

### Task 9: Haiku-классификатор (сетевой seam)
**Files:** Create `app/services/intent_classifier.py` (или секция `intent_router.py` с инъекцией клиента); Test (мок `messages.create`).
- [ ] Тест: `classify_intent_llm(text, allowed_cmds) -> Optional[str]` возвращает валидную команду из списка или None; неон-команду (галлюцинация вне списка) отбрасывает в None (зуб).
- [ ] FAIL → реализация по образцу `quick_answer.py:95-100`, модель `claude-haiku-4-5-20251001`, system-промт «верни ровно одну команду из списка или NONE, не выдумывай», max_tokens≈16 → PASS. Commit.

### Task 10: Учёт стоимости IR-2
**Files:** Modify классификатор; Test.
- [ ] Тест: успешный Haiku-вызов пишет `cost_tracker.record_cost(user_id, username, ~0.002)` (spy); фейл/None — не пишет лишнего; (опц.) `check_limit` перед вызовом.
- [ ] FAIL → обвязка ledger → PASS. Commit.

### Task 11: run_intent ветка ir_uncertain (admin-only)
**Files:** Modify `bot.py`; Test.
- [ ] Тест: `ir_uncertain` под admin→зовётся `classify_intent_llm`, при cmd→`_ir_send_confirm` (всегда confirm, даже для FREE — Haiku-догадка), при None→`_ir_unknown`; под friend ветка `ir_uncertain` недостижима (friend-путь её не порождает — зуб).
- [ ] FAIL → реализация → PASS. Commit.

### Task 12: Каскад-интеграция (экономия)
**Files:** Test only.
- [ ] Тест: IR-1 HIGH→`classify_intent_llm` НЕ зовётся (0 вызовов, spy); IR-1 UNCERTAIN+admin→зовётся 1 раз; friend UNCERTAIN→0 вызовов. Commit.

## 8. Мердж-ворота
- Полный регресс перед мерджем (правило `_devtask_run_regress`/[Мердж]-гейт): `NEW=0` к baseline.
- Затронутый быстрый регресс: `tests/test_intent_router*.py` + существующие `classify_message`-тесты (доказать нерегресс каскада) + observe (если трогали общий dispatch).
- Живой тап после рестарта: 3 фразы брифа (admin), 1 friend-фраза (разрешённая + запрещённая), 1 непонятная (честный фолбэк), 1 платная (кнопка, не авто-exec).

---

## Допущения (неоднозначности брифа — решены дефолтом, ждут подтверждения)

1. **Расширять, не заменять** существующий keyword-каскад (0.1). Роутер видит только остаточный «бы-chat» текст. Если Артём хочет, чтобы конкретные фразы (напр. «фото блюда») перебивали существующий `generate`-триггер — это тюнинг приоритета триггеров, вынесен в IR-3/аудит.
2. **Точка вставки — хвост `classify_message`** (после всех триггеров), не раньше — ради инварианта «не сломать флоу». Цена: фразы, уже пойманные широким существующим триггером, до роутера не дойдут (осознанный трейд-офф, тюним алиасами/приоритетом позже).
3. **chat→research (платный Perplexity) удаляется из основного пути** и заменяется честным фолбэком. Если нужно «отвечать на любой вопрос из интернета» — остаётся явная `/research` и триггер `research`. Считаю это желаемым (бриф: «НЕ угадывание» + закрытие money-дыры).
4. **IR-2 (Haiku) — только admin.** Не тратим friend-cap на классификацию; friend получает IR-1 + честный фолбэк. (Можно включить friend-IR-2 под `check_limit` позже — отдельным решением.)
5. **auto-exec ⟺ `cmd ∈ FREE_AUTOEXEC`** (курируемый белый список), НЕ `action=="exec"` из реестра (там exec = «без параметров», не «бесплатно»). Платное структурно не может авто-исполниться (`FREE_AUTOEXEC ∩ PAID == ∅`). `/regress` вынесен в confirm (тяжёлый).
6. **Параметры (arg) — в `state["pending_ir"]`, не в callback_data** (лимит 64 б, кириллица). IR-1/IR-2 работают без параметров (`arg=""`); параметры — предмет IR-3.
7. **`FRIEND_ALLOWED_COMMANDS` прокидывается в `intent_router` параметром/константой**, модуль не импортирует `bot.py` (симметрия изоляции с `jarvis_menu.py`).
8. **Пороги score (0.72/0.15/0.45) — стартовые**, финальная калибровка — после живого прогона на реальных фразах Артёма (как «Даниил редактирует тексты после живого теста» в menu-арке).
9. **`ALIASES` — главный рычаг точности**; стартовый набор в 2.1 расширяется итеративно по логам реальных промахов (лог непопаданий `ir_unknown`/`ir_uncertain` в structured_logger для последующего дообогащения).
10. **Модель Haiku:** `claude-haiku-4-5-20251001` (как в `quick_answer.py`); при желании — вынести в env `JARVIS_IR_MODEL`.

## Открытые решения для Артёма/Daniil (до кода)
- [ ] Ок на «расширять, не заменять» + удаление chat→research-угадывания (Допущение 1,3)?
- [ ] Ок на IR-2 только-admin (Допущение 4)?
- [ ] Стартовый `FREE_AUTOEXEC` (4) — согласовать список (особенно: `/regress` в confirm, а не авто; `/costs` авто — ок?).
- [ ] Порог автономности friend-свободного-текста (3.2): включаем сразу в IR-1 или отдельной под-ступенью после admin-обкатки?

---

## Self-Review (по чеклисту writing-plans)
- **Покрытие брифа:** вопрос 1→0.1; 2а→0.2/2.1; 2б→0.3; 3→0.4; 4→0.5/§4; 5→§5. Все пять закрыты фактом + задачами.
- **Плейсхолдеры:** сигнатуры и точки вставки конкретны (`file:line`); реализация скорера/фолбэка расписана до уровня порогов и веток. Это СПЕКА (ждёт ОК) — детальные code-блоки шагов дозаполняются исполнителем по TDD-контракту задач.
- **Согласованность имён:** `resolve_offline`, `IRResult`, `auto_exec_ok`, `is_paid`, интенты `ir_route/ir_clarify/ir_uncertain/ir_unknown`, callback `ir:run/ir:pick/ir:cancel`, `state["pending_ir"]`, `FREE_AUTOEXEC/PAID/PRICE/ALIASES` — единообразны по всему документу.
