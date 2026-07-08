# «Доступ для друга» (мульти-юзер) — дизайн

**Дата:** 2026-06-23
**Ветка:** phase-4.0-unified-jarvis
**Контекст:** Бот сейчас одно-пользовательский (мой chat_id). Нужно дать доступ другу
на **моём же** боте (не отдельный инстанс), под денежным лимитом, с железной
изоляцией от админ-функций и ключей, и с флоу-запросом доступа через кнопки мне.

Источник дизайна: brainstorming, согласовано 7 секций (см. «Принятые решения»).

> ⚠️ **Эта спека написана ПОСЛЕ сверки с кодом.** Три исходных предположения дизайна
> оказались неточны — поправки вынесены в раздел «Сверка с кодом» и меняют объём
> (часть уже сделана, whitelist уже существует). Читать раздел сверки обязательно.

---

## Сверка с фактическим кодом (2026-06-23)

| Элемент | Где | Состояние по факту |
|---|---|---|
| **Whitelist-инфраструктура** | `app/services/auth/whitelist.py` | **Уже есть.** `is_allowed(user_id)`, `load_admin_user_id()` (env `JARVIS_ADMIN_USER_ID`), `load_allowed_user_ids()` (env `JARVIS_ALLOWED_USER_IDS`, CSV), `REJECT_MESSAGE`. Env читается **лениво** на каждый вызов (ротация без рестарта). |
| Гейт двери | `tools/jarvis_smart_telegram_control.py:5700` `_whitelist_gate(upd)`; зовётся в `process_update` `:6293` | Пускает/режет по `is_allowed`; на reject шлёт `REJECT_MESSAGE` + audit-событие `whitelist_rejected`. **Кнопок/pending пока нет.** |
| **Bootstrap-админ (страховка #5)** | `whitelist.load_admin_user_id()` ← env `JARVIS_ADMIN_USER_ID` | **Уже частично есть.** Админ определяется из env, не из файла → самоблок невозможен, если сохраним env-first. |
| **Feature-гейты на единственный chat** | `jarvis_smart_telegram_control.py` `:1351, :5542, :6326, :6330, :6335, :6345, :6354, :6364, :6306` | **РЕАЛЬНЫЙ БЛОКЕР friend.** Каждый интерсептор (swapbatch, persona, video, face-swap, файлы) гейтится на `chat_id == ALLOWED_CHAT_ID`. Friend проходит дверь, но не достаёт функций. Это и есть «реворк ALLOWED_CHAT_ID». |
| Per-user ледджер (видимость) | `app/services/audit/cost_tracker.py` | `record_cost(user_id, username, amount)`, daily/monthly/all-time в **Kyiv TZ**, `state/cost_tracking.json`, атомарная запись. **Докстринг: «no hard cap — only reports, never blocks».** Команды `/my_stats`, `/admin_costs`. |
| Глобальный лимит-трекер | `app/services/block_m_common/cost_tracker.py` | `CostTracker` (класс), **глобальный** дневной кап `DAILY_LIMIT_USD=10.0`, `DailyLimitExceeded`, `state/personas/expenses.jsonl`, **UTC**-дни. Зовут `persona_handler`, `video_generator`. **Не per-user.** |
| **Свап/animate траты в ледджер (#2)** | `app/handlers/face_swap_handler.py:747` (animate), `:778` (`_bill_completed_videos`: свап+animate) | **УЖЕ пишутся** в per-user `audit/cost_tracker`. Память «только LLM» — **устарела**. Остаётся аудит покрытия остальных платных путей. |
| LLM траты в ледджер | router `:346-348`, мост `:6182/:6204/:6036` | Пишутся в `audit/cost_tracker`. |
| Существующие админ-команды | мост `:5811-5844` | `/my_stats` (любой whitelisted), `/admin_costs` (gated на `load_admin_user_id()`). |
| Audit-лог | `state/audit/` (существует) | Есть инфраструктура событий (`whitelist_rejected` уже пишется). Переиспользуем для activity-лога. |
| `state/users.json` | — | **Нет.** Новый файл membership/role/limit/status. |

---

## Принятые решения (согласовано в brainstorming)

1. **Один бот, мульти-юзер** (не отдельный инстанс для друга).
2. **Хранение:** `state/users.json` — членство/роль/лимит/статус. Траты **не дублируем** —
   берём из существующего `audit/cost_tracker` (он уже per-user). Env — только для
   **bootstrap-админа** (меня), защита от самоблока.
3. **Роли:** `admin` (я: всё + безлимит + наблюдение), `friend` (все генеративные функции
   под $-лимитом; видит только своё; НЕ видит admin-команды/ключи/мои сессии).
4. **Флоу запроса доступа (ключевая хотелка):** неизвестный пишет боту → вежливый отказ +
   запись в `pending` → **мне** уведомление с кнопками `[✅ Добавить] [❌ Отклонить]` →
   тап approve (дефолтный лимит **$5/день**) / reject. Дедуп: повторные запросы не спамят.
   Callback: `access:approve:<id>` / `access:reject:<id>`.
5. **Лимит:** $-лимит, настраиваемый мной, сброс **ежедневно (Kyiv)** + по запросу.
   **Жёсткий блок ДО траты** (friend + потрачено-сегодня + оценка-операции > лимит → отказ
   ДО генерации) + пинг мне.
6. **Админ-команды:** `/admin_users`, `/admin_setlimit <id> <$>`, `/admin_resetlimit <id>`,
   `/admin_activity <id>`, `/admin_costs` (есть).
7. **Activity-лог:** детальный (действие / промт / движок / исход / $) поверх `state/audit/`.

---

## Критичные страховки (обязательны, проверяемы)

1. **БЕЗОПАСНОСТЬ / изоляция.** `friend` НЕ видит и НЕ может вызвать: ключи
   (WaveSpeed/Replicate/OpenAI), мои сессии/настройки, admin/restart/деплой-команды,
   чужую статистику. Проверка ролей — на входе каждой привилегированной ветки, default-deny.
2. **Траты в per-user ледджер (аудит покрытия).** Все платные пути friend (свап-батч,
   animate-батч, одиночный `/animate`, video-face-swap, persona-video если доступна)
   **обязаны** звать `audit/cost_tracker.record_cost(user_id, …)`. Свап+animate-батч уже
   зовут (`face_swap_handler:747/778`); остальное — проверить и добить. Иначе лимит слеп.
3. **Унификация трекеров — без третьего.** Per-user лимит-гейт строится **поверх
   `audit/cost_tracker`** (источник per-user трат). `block_m_common/cost_tracker` остаётся
   глобальным предохранителем как есть. Новый код **не заводит** третий ледджер; лимиты
   живут в `users.json`, потраченное читается из `audit/cost_tracker.get_user_stats`.
4. **Лимит ДО траты, не после.** Гейт срабатывает **перед** отправкой платного запроса
   в движок (оценка стоимости операции + сегодняшние траты vs лимит). Не «постфактум».
5. **Bootstrap-админ из env — я НИКОГДА не заблокируюсь.** `admin` резолвится env-first
   (`JARVIS_ADMIN_USER_ID`); `users.json` не может понизить/удалить bootstrap-админа.

---

## Архитектура

### Хранилище: `state/users.json` (новый модуль `app/services/auth/users_store.py`)

```json
{
  "users": {
    "237616472": {
      "role": "admin",
      "username": "daniil",
      "status": "active",
      "daily_limit_usd": null,
      "added_by": "bootstrap",
      "added_at": "2026-06-23T12:00:00+03:00"
    },
    "555000111": {
      "role": "friend",
      "username": "petya",
      "status": "active",
      "daily_limit_usd": 1.0,
      "added_by": "237616472",
      "added_at": "2026-06-23T13:30:00+03:00"
    }
  },
  "pending": {
    "777888999": {
      "username": "stranger",
      "first_request_at": "2026-06-23T14:00:00+03:00",
      "request_count": 3
    }
  }
}
```

- **Атомарная запись** (`.tmp` + `os.replace`) и in-process `RLock` — копируем контракт из
  `audit/cost_tracker.py` (тот же паттерн, тот же стиль).
- **Время** — Kyiv TZ (как `audit/cost_tracker`), для согласованного дневного rollover.
- `daily_limit_usd: null` = безлимит (только admin).
- Модуль — **чистые функции/класс без сетевого I/O**, легко тестируется на моках.

**API (черновой контракт, финализируется в плане):**
- `get_role(user_id) -> "admin" | "friend" | None` — env-first для admin (страховка #5).
- `is_member(user_id) -> bool`
- `get_limit(user_id) -> float | None`
- `add_friend(user_id, username, *, added_by, limit_usd=1.0)`
- `set_limit(user_id, limit_usd)`
- `set_status(user_id, "active"|"blocked")`
- `add_pending(user_id, username) -> bool` (True если новый; дедуп инкрементит `request_count`)
- `pop_pending(user_id) -> dict | None`
- `list_users() -> list[dict]`

### Резолюция роли (страховки #1, #5)

```
get_role(user_id):
    if user_id == env JARVIS_ADMIN_USER_ID:  return "admin"     # bootstrap, env-first
    rec = users.json["users"].get(user_id)
    if rec and rec.status == "active":        return rec.role
    return None
```

Whitelist (`whitelist.is_allowed`) переключается на «членство в `users.json` ИЛИ env-admin
ИЛИ env-CSV (обратная совместимость)». Открытый режим (оба env пустые + пустой users.json)
сохраняем, чтобы не сломать dev.

### Реворк feature-гейтов (фаза 2, главный объём)

Заменить разбросанные `chat_id == ALLOWED_CHAT_ID` на семантические проверки:
- **Генеративные функции** (swapbatch, animate, persona, video, face-swap, приём файлов) →
  `is_member(user_id)` (admin ИЛИ active-friend).
- **Админ-функции** (restart, деплой, ключи, чужая статистика, `/admin_*`) →
  `get_role(user_id) == "admin"`. Default-deny.

Внимание: `ALLOWED_CHAT_ID` сейчас сравнивается со **строкой**; роль резолвим по
**user_id** (для групп ≠ chat_id). Берём `user_id` из апдейта (как `_whitelist_gate` уже делает).

### Флоу запроса доступа (решение #4)

```
process_update → _whitelist_gate:
    if is_member(user_id): pass
    else:
        is_new = users_store.add_pending(user_id, username)   # дедуп
        send(user, REJECT_MESSAGE)                            # вежливый отказ
        if is_new:                                            # не спамим админа на повторах
            send(admin, "🔔 Запрос доступа: @username (id=...)",
                 reply_markup=[[✅ Добавить → access:approve:<id>],
                               [❌ Отклонить → access:reject:<id>]])
        audit("access_requested", user_id, request_count)
```

Callback-роутинг (рядом с `sbeng:` из Фазы B), **только для admin**:
- `access:approve:<id>` → `add_friend(id, …, limit_usd=$1/день)` + `pop_pending` →
  уведомить друга «доступ открыт» + подтвердить админу.
- `access:reject:<id>` → `pop_pending` (или `status=blocked`) → подтвердить админу.

### Лимит-гейт ДО траты (решения #5, страховка #4)

Точка: **перед** каждым платным движковым вызовом для friend (admin — bypass).

```
limit_gate(user_id, estimated_usd) -> (allowed: bool, reason: str):
    if get_role(user_id) == "admin": return True
    limit = get_limit(user_id)
    if limit is None: return True
    spent_today = audit.cost_tracker.get_user_stats(user_id)["today"]   # Kyiv
    if spent_today + estimated_usd > limit:
        ping_admin("friend X уперся в лимит: $spent + $est > $limit")
        return False, "лимит на сегодня исчерпан"
    return True
```

- Оценка `estimated_usd` берётся из `caps_for(engine).cost_for(seconds, resolution)` (свап/animate
  уже так считают для биллинга — переиспользуем) и аналогов для прочих операций.
- Сброс: дневной автоматически (Kyiv rollover в `audit/cost_tracker` уже по дням) +
  ручной `/admin_resetlimit <id>` (обнуляет сегодняшний bucket пользователя или ставит
  override — решаем в плане; предпочтительно override-«прощение» без потери истории).

### Activity-лог (решение #7)

Поверх `state/audit/`. На каждое значимое действие friend писать запись:
`{ts, user_id, username, action, engine, prompt_excerpt, outcome, cost_usd}`.
`/admin_activity <id>` — последние N записей по пользователю. Промт — **обрезанный**
(privacy + размер). Не логируем ключи/токены никогда.

---

## Фазы реализации (TDD на моках, как Этап 2/3 Фазы B)

| Фаза | Содержание | Траты |
|---|---|---|
| **1. `users_store`** | Новый модуль + `state/users.json`, атомраная запись, role-резолюция env-first, дедуп pending. Юнит-тесты. | $0 |
| **2. Разблокировка friend** | Реворк `chat_id == ALLOWED_CHAT_ID` → `is_member`/`role==admin`. `whitelist.is_allowed` поверх `users_store`. Default-deny на админ-ветках. | $0 |
| **3. Access-флоу с кнопками** | `add_pending`+дедуп, уведомление админу с inline-кнопками, callback `access:approve/reject`, уведомления другу. | $0 |
| **4. Лимит-гейт + траты в ледджер** | `limit_gate` ДО траты; **аудит покрытия** record_cost по всем платным путям friend (страховка #2); пинг админу на блоке. | $0 (моки) |
| **5. Админ-команды** | `/admin_users`, `/admin_setlimit`, `/admin_resetlimit`, `/admin_activity` (+ `/admin_costs` есть). Все gated на `role==admin`. | $0 |
| **6. Activity-лог** | Детальные записи поверх `state/audit/`; чтение для `/admin_activity`. | $0 |
| **7. Живой smoke** | ⛔ **СТОП, зову пользователя.** Друг шлёт боту → pending → кнопки мне → approve → друг генерит под лимитом → упирается в лимит → пинг. Проверка изоляции (friend не видит admin). | мин. ($1 лимит) |

Между задачами — ревью субагентом (как в Фазе B). Код — субагентами по плану.

---

## Вне области (YAGNI сейчас)

- Веб-дашборд управления юзерами (хватает команд + кнопок).
- Месячные/недельные лимиты (пока только дневной; структура `users.json` расширяема).
- Роли сверх admin/friend (напр. «read-only观察»).
- Перенос `block_m_common/cost_tracker` на per-user (он остаётся глобальным предохранителем).
- Сквозное шифрование `users.json` (файл на моей машине; защита — ОС/диск).
- Самообслуживание лимита другом (только admin меняет лимиты).

---

## Решённые вопросы (согласовано 2026-06-23)

1. **`/admin_resetlimit`** → **override** («простить» сегодняшние траты кредитом, история
   в `cost_tracking.json` цела). Лимит-гейт вычитает override из `spent_today`.
2. **Reject в access-флоу** → **`status=blocked`** (тихо игнорить, не спамит; админ может
   разблокировать).
3. **Дефолтный лимит friend на approve** → **$5/день** (\$1 = одно WaveSpeed-видео, друг
   сразу упрётся; бюджет не проблема). Меняется командой `/admin_setlimit`.
4. **Текст превышения** → мягкий: «Дневной лимит \$X исчерпан, напиши Даниилу если нужно больше».
5. **Объём friend-доступа** → **swapbatch/animate + persona + video_face_swap** (не только своп).
   ⚠️ Следствие: persona/me-persona пишут траты только в глобальный трекер — план добавляет
   per-user dual-write, иначе лимит слеп на persona (страховка #2/#3).
