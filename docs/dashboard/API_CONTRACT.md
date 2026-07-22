# API_CONTRACT (черновик): дашборд chatter

**Дата:** 2026-07-22 (обновлён под решения §11) · Дополняет `ARCHITECTURE.md`. Черновик — сигнатуры и формы, не финал. Все ручки под auth (session-cookie + 2FA), за ENFORCE-middleware, **публичных ручек нет**. Каждая ручка проверяет скоуп (ресурс принадлежит клиенту вызывающего). Ошибки — единый формат `{ "error": { "code", "message" } }`. Session-файлы/ключи/`.env` наружу не отдаются никогда; ключи маскированы, повторно не показываются.

**Модель ресурсов** (§4/§8): `client → N accounts`. Операционные ручки (конфиг, тумблеры, лента, статус, usage) — на уровне **account**. Биллинг/юнит-экономика — роллап на уровень **client**. Вкладки экрана аккаунта: Агент (§4b) · Знання/Playbook (§4) · Приклади (§4c) · Тумблери (§5) · Канали (§2–3) · Інтеграції (§9) · Аналітика (§8).

Базовый префикс: `/api/v1`.

---

## 1. Auth (роли owner/client, скоуп с первого дня)

| Метод | Путь | Тело / ответ |
|---|---|---|
| POST | `/api/v1/auth/login` | `{login, password}` → `{totp_required:true}` или set-cookie |
| POST | `/api/v1/auth/2fa` | `{totp_code}` → set-cookie + `{user_id, role}` |
| POST | `/api/v1/auth/logout` | → 204 |
| GET | `/api/v1/auth/me` | → `{user_id, login, role(owner\|client), client_id?}` |

> MVP — редактирует только `owner` (§11 A11). Роль `client` заложена в схеме/скоупе, включается в v2 без миграции.

---

## 2. Клиенты (биллинг) + аккаунты (control-plane)

**Клиенты (владелец видит всех своих; client — только себя):**
| Метод | Путь | Назначение |
|---|---|---|
| GET | `/api/v1/clients` | `[{id, display_name, tariff, accounts_count, health_rollup}]` |
| POST | `/api/v1/clients` | `{display_name, tariff}` → `{id}` (тариф — из конфиг-сетки, не произвольный) |
| GET | `/api/v1/clients/{cid}` | Карточка клиента + список аккаунтов (без секретов). |
| GET | `/api/v1/clients/{cid}/accounts` | `[{id, slug, tg_ref, enabled, runner_state, heartbeat_age_s, health, connected_via}]` |

**Аккаунты (1:1 с раннером):**
| Метод | Путь | Назначение |
|---|---|---|
| GET | `/api/v1/accounts/{aid}` | Карточка аккаунта (без секретов; `session_ref` — только флаг «подключён», маскирован). |
| POST | `/api/v1/accounts/{aid}/start` | `enabled=true` + сигнал гардиану. → `{runner_state}` (async: pending→running). |
| POST | `/api/v1/accounts/{aid}/stop` | `enabled=false` + сигнал. → `{runner_state}`. |
| POST | `/api/v1/accounts/{aid}/revert` | Откат конфига на предыдущий `.versions/`-снимок. → `{version_ts}`. |
| POST | `/api/v1/accounts/{aid}/disconnect` | Отключить аккаунт (стереть session_ref, аудит-лог). → 204. |
| GET | `/api/v1/accounts/{aid}/status` | `{runner_state, heartbeat_age_s, pid_alive, funnel_gate, honesty_mode, kill_switch}`. |

> `runner_state` ∈ `stopped | pending | running | degraded`. start/stop **асинхронны**: дашборд декларирует желаемое, гардиан исполняет (ARCHITECTURE §4). UI поллит `status` или слушает SSE (§7).

---

## 3. Онбординг-визард (М2) — три пути подключения

Создание аккаунта под клиентом + прохождение 4 блоков:

| Метод | Путь | Назначение |
|---|---|---|
| POST | `/api/v1/clients/{cid}/onboarding/accounts` | Черновик аккаунта (slug, display) → скаффолд 4 файлов (`create_client.py`). → `{aid}`. |
| GET | `/api/v1/onboarding/templates?vertical=` | Playbook-шаблоны по вертикали (услуги/продажи/недвижимость/агентства/ивенты). |

**Путь A — QR (default):**
| POST | `/api/v1/accounts/{aid}/connect/qr/start` | → `{login_id, qr_png_b64}` |
| POST | `/api/v1/accounts/{aid}/connect/qr/poll` | `{login_id}` → `{connected}` \| `{needs_password:true}` (2FA) |

**Путь B — код на номер (fallback):**
| POST | `/api/v1/accounts/{aid}/connect/code/start` | `{phone}` → `{login_id, needs_code:true}` |
| POST | `/api/v1/accounts/{aid}/connect/code/verify` | `{login_id, code, password?(2FA)}` → `{connected}` \| `{needs_password:true}` |

**Путь C — по ключу (v2, ТОЛЬКО после P1/P2):**
| POST | `/api/v1/accounts/{aid}/connect/key` | `{session_string?, api_id?, api_hash?}` → **проба коннекта** → `{connected}` \| `{error}` (понятная ошибка, не тихий фейл). Ключ шифруется at rest, маскируется, повторно не отдаётся. |
| POST | `/api/v1/clients/{cid}/onboarding/bulk` | Пачка аккаунтов `[{slug, session_string, persona_ref}]` → массовое подключение (v2). |

⚠️ Все пути: `.session`/ключ шифруются at rest; пароль/2FA клиента **не храним**; каждое connect/disconnect → аудит-лог (§10).

**Блоки 2–4 (общие):**
| PUT | `/api/v1/accounts/{aid}/persona` | `{persona_md}` → `persona.md` + валидация. |
| PUT | `/api/v1/accounts/{aid}/knowledge` | `{sources:[{type:file\|text\|url, value}]}` → `knowledge.md`. |
| PUT | `/api/v1/accounts/{aid}/playbook` | `{template_id}` \| `{playbook_md}` → `playbook.md`. |
| POST | `/api/v1/accounts/{aid}/onboarding/finish` | Снапшот `.versions/` + регистрация (enabled=false). → `{aid, ready:true}`. |

---

## 4. Редактор конфига (М3)

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/api/v1/accounts/{aid}/config` | `{persona_md, knowledge_md, playbook_md, settings_yaml, examples_yaml}` (settings — без секретов). |
| PUT | `/api/v1/accounts/{aid}/config/{file}` | `file` ∈ persona\|knowledge\|playbook\|settings\|examples. `{content}`. Прогон через `loader.py`-валидацию; ошибка → тот же текст, что у раннера. Успех → снапшот `.versions/` + `/reload`. |
| GET | `/api/v1/accounts/{aid}/versions` | `[{ts, changed_files}]`. |
| POST | `/api/v1/accounts/{aid}/versions/{ts}/restore` | Восстановить снимок + reload. |

---

## 4b. Агент (М7) — редактор персоны + красные линии

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/api/v1/accounts/{aid}/agent` | `{persona_md, style_rules:[...], red_lines:{strict_knowledge, safe_payment, deadline_guard}}` (red_lines — из `settings.yaml`). |
| PUT | `/api/v1/accounts/{aid}/agent/persona` | `{persona_md, style_rules?}` → валидация (`loader.py`) → `persona.md` + снапшот `.versions/` + reload. |
| PUT | `/api/v1/accounts/{aid}/agent/red_lines` | `{strict_knowledge?, safe_payment?, deadline_guard?}` — **переключатели**, пишут `settings.yaml` (не трогая прозу). |
| GET | `/api/v1/accounts/{aid}/agent/preview` | Read-only сборка итогового системного промпта (`build_system_prompt` серверно) — что реально уйдёт в модель. → `{system_prompt, token_estimate}`. |

> Откат — через общие `/versions` (§4). Красные линии отдельными флагами, чтобы клиент не снёс их случайным редактированием текста.

## 4c. Приклади діалогів (М8) — few-shot

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/api/v1/accounts/{aid}/examples` | `{examples:[{id, tag, priority, enabled, turns:[{role, text}]}], selected_preview:{block, token_count, budget}}`. |
| PUT | `/api/v1/accounts/{aid}/examples` | Полный набор → валидация → `examples.yaml` + снапшот `.versions/` + reload. |
| POST | `/api/v1/accounts/{aid}/examples/item` | `{tag, priority, enabled, turns}` → добавить один пример. |
| PATCH | `/api/v1/accounts/{aid}/examples/{ex_id}` | Правка пары/тега/приоритета/enabled. |
| DELETE | `/api/v1/accounts/{aid}/examples/{ex_id}` | Удалить пример. |
| GET | `/api/v1/accounts/{aid}/examples/selection` | Что реально попадёт в промпт: статический отбор (enabled + покрытие тегов + priority, срез по бюджету ~1200–1500 ток). → `{selected:[ex_id...], token_count, dropped_by_budget:[...]}`. |

**Импорт истории Telegram (v1.5):**
| POST | `/api/v1/accounts/{aid}/examples/import/start` | multipart: экспорт Telegram (JSON). → авто-нарезка на пары + **анонимизация** (имена/телефоны/@-хендлы/ссылки → плейсхолдеры) → `{import_id, candidates:[{idx, turns, suggested_tag}]}`. |
| POST | `/api/v1/accounts/{aid}/examples/import/{import_id}/commit` | `{keep:[{idx, tag, priority}]}` (экран «оставить/выкинуть») → добавляет выбранные в `examples.yaml`. Аудит-лог (P8: импорт ПДн третьих лиц). |

> Приватность: анонимизация — ДО показа candidates и ДО записи. Импорт — только по явному действию, аудируется. Связь с P1/P2 (примеры в незашифрованном конфиге).

## 5. Тумблеры (М4) — поверх `yaml_edit.py`

| Метод | Путь | Тело | Примечание |
|---|---|---|---|
| POST | `/api/v1/accounts/{aid}/toggles/honesty` | `{mode: honest\|free_owner_liability, confirm:true, client_name}` | free требует consent + имя клиента (как `/honesty ... confirm`). Дефолт honest. **Активных инструкций сокрытия не добавляем** — тумблер лишь снимает перехват, поведение как есть. |
| POST | `/api/v1/accounts/{aid}/toggles/funnel_gate` | `{on: bool, confirm:true}` | on требует confirm; ответ включает `catchup_warning` (ARCHITECTURE §4). |
| POST | `/api/v1/accounts/{aid}/toggles/pause` | `{scope: global\|contact, contact_ref?, until_s?}` | global = kill_switch; contact = mute. |
| POST | `/api/v1/accounts/{aid}/toggles/resume` | `{scope, contact_ref?}` | |
| PUT | `/api/v1/accounts/{aid}/allowlist` | `{allow:[...], deny:[...]}` | Пишет `telegram.allowlist/denylist` + reload. |

---

## 6. Лента / inbox (М5)

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/api/v1/accounts/{aid}/feed` | `[{event, contact_ref, summary, ts, actions:[...]}]` (эскалации, takeover, деградации, bot_question). |
| POST | `/api/v1/accounts/{aid}/feed/{contact_ref}/reply` | `{text}` → раннер шлёт лиду (= сообщение персоны), ставит takeover-паузу. |
| POST | `/api/v1/accounts/{aid}/feed/{contact_ref}/action` | `{action: resume\|snooze\|stop\|keep}` — те же callbacks, что в control-bot. |
| GET | `/api/v1/accounts/{aid}/dialog/{contact_ref}` | Полная переписка (аудируемый доступ к ПДн, P8) — только по явному действию. |

---

## 7. Real-time (SSE)

| Метод | Путь | Поток |
|---|---|---|
| GET | `/api/v1/accounts/{aid}/stream` | SSE: `status_changed`, `feed_item`, `escalation`, `degradation`. |
| GET | `/api/v1/stream` | SSE-агрегат по всем аккаунтам в скоупе вызывающего (для обзора М1). |

---

## 8. Аналитика + учёт расхода (М6)

**По аккаунту:**
| Метод | Путь | Ответ |
|---|---|---|
| GET | `/api/v1/accounts/{aid}/metrics?window=7d` | `{dialogs, leads, escalations, avg_response_s, funnel:{new,qualifying,hot,escalated,closed}, bot_questions, tokens:{input,output,cache_read}, cost_usd}`. |

**Роллап по клиенту (юнит-экономика):**
| GET | `/api/v1/clients/{cid}/billing?window=30d` | `{tariff, dialogs, tokens:{input,output,cache_read}, cost_usd, cost_by_account:[...], margin_hint}`. |
| GET | `/api/v1/billing/overview` | Кросс-клиентский обзор для владельца (расход/тариф/маржа по всем клиентам). |

> `cost_usd` считается по **прайсу из конфига** (§11 A7), не хардкод. `cache_read` = 0 до prompt-caching (P3), > 0 после — видно экономию.

---

## 9. Событийная шина + адаптеры (ARCHITECTURE §6)

**Контракт события** (во все адаптеры):
```
{
  "event": "lead.created | lead.qualified | lead.escalated | lead.status_changed | dialog.taken_over | dialog.resumed | bot_question.asked",
  "account_id": "...",
  "client_id": "...",
  "contact_ref": "...",
  "ts": 1750000000.0,
  "payload": { ... }
}
```

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/api/v1/accounts/{aid}/integrations` | Список адаптеров аккаунта. |
| POST | `/api/v1/accounts/{aid}/integrations` | `{type: webhook\|sheets\|amocrm\|csv, config}` (MVP: webhook, sheets). |
| DELETE | `/api/v1/accounts/{aid}/integrations/{int_id}` | Отключить. |
| POST | `/api/v1/accounts/{aid}/integrations/{int_id}/test` | Тестовое событие → проверка доставки. |

**Интерфейс адаптера (плагин):** `emit(event: Event) -> DeliveryResult`. Webhook: POST + HMAC-подпись (паттерн `N8N_JARVIS_WEBHOOK_SECRET`). Sheets: append (`GOOGLE_SERVICE_ACCOUNT_JSON`). amoCRM/Kommo/CSV — тот же интерфейс, v2.

---

## 10. Аудит

| Метод | Путь | Ответ |
|---|---|---|
| GET | `/api/v1/audit?window=30d` | `[{user, action, target, detail, ts}]` — кто что переключил, **кто какой аккаунт подключил/отключил**. Только владелец. |

---

## Заметки

- `contact_ref` — псевдонимизированные ссылки, не сырые Telegram-id в URL без нужды.
- start/stop/connect — **асинхронные**: возвращают состояние-в-процессе, финал через SSE/поллинг `status`.
- Валидация конфига — единственный источник правды `loader.py` (не дублировать в дашборде).
- Прайс LLM для `cost_usd` — конфигом; тарифная сетка клиентов — конфигом (ARCHITECTURE §11 A2/A7).
- OpenAPI-схема генерируется FastAPI автоматически — этот черновик станет живым `/docs`.
