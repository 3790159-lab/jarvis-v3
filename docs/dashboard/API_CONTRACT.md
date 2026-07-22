# API_CONTRACT (черновик): дашборд chatter

**Дата:** 2026-07-22 · Дополняет `ARCHITECTURE.md`. Черновик — сигнатуры и формы, не финал. Все ручки под auth (session-cookie), за ENFORCE-middleware. Ошибки — единый формат `{ "error": { "code", "message" } }`. Ничего из session-файлов/`.env` наружу не отдаётся никогда.

Базовый префикс: `/api/v1`.

---

## 1. Auth

| Метод | Путь | Тело / ответ |
|---|---|---|
| POST | `/api/v1/auth/login` | `{login, password}` → set-cookie + `{user_id, role}` |
| POST | `/api/v1/auth/logout` | → 204 |
| GET | `/api/v1/auth/me` | → `{user_id, login, role}` |

---

## 2. Клиенты (М1) + control-plane (§4)

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/api/v1/clients` | Список: `[{id, slug, display_name, enabled, runner_state, heartbeat_age_s, last_activity_ts, health}]`. `health` ∈ green/yellow/red. |
| GET | `/api/v1/clients/{id}` | Карточка клиента (без секретов; `session_path` — только флаг «подключён», не путь). |
| POST | `/api/v1/clients/{id}/start` | Декларирует `enabled=true` + сигнал гардиану. → `{runner_state}` (async: pending→running). |
| POST | `/api/v1/clients/{id}/stop` | `enabled=false` + сигнал. → `{runner_state}`. |
| POST | `/api/v1/clients/{id}/revert` | Откат конфига на предыдущий `.versions/`-снимок (`/rollback`). → `{version_ts}`. |
| GET | `/api/v1/clients/{id}/status` | Живой статус: `{runner_state, heartbeat_age_s, pid_alive, funnel_gate, honesty_mode, kill_switch}`. |

> `runner_state` ∈ `stopped | pending | running | degraded`. start/stop **асинхронны**: дашборд декларирует желаемое, гардиан приводит реальность (ARCHITECTURE §4). UI поллит `status` или слушает SSE (§7).

---

## 3. Онбординг-визард (М2)

| Метод | Путь | Назначение |
|---|---|---|
| POST | `/api/v1/onboarding/clients` | Создать черновик клиента (slug, display_name) → скаффолд 4 файлов (`create_client.py`). → `{id}`. |
| POST | `/api/v1/onboarding/clients/{id}/tg/connect/start` | Начать логин аккаунта. → `{login_id, qr_png_b64?}` (QR) или `{login_id, needs_code:true}` (код). |
| POST | `/api/v1/onboarding/clients/{id}/tg/connect/code` | `{login_id, code, password?(2FA)}` → `{connected:true}` \| `{needs_password:true}`. `.session` пишется на сервере, наружу не отдаётся. |
| PUT | `/api/v1/onboarding/clients/{id}/persona` | `{persona_md}` → пишет `persona.md` + валидация. |
| PUT | `/api/v1/onboarding/clients/{id}/knowledge` | `{sources:[{type:file\|text\|url, value}]}` → собирает `knowledge.md`. |
| PUT | `/api/v1/onboarding/clients/{id}/playbook` | `{template_id}` \| `{playbook_md}` → пишет `playbook.md`. |
| GET | `/api/v1/onboarding/templates` | Список playbook-шаблонов (под сегмент «персональные продавцы»). |
| POST | `/api/v1/onboarding/clients/{id}/finish` | Снапшот `.versions/` + регистрация в реестре (enabled=false). → `{id, ready:true}`. |

---

## 4. Редактор конфига (М3)

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/api/v1/clients/{id}/config` | `{persona_md, knowledge_md, playbook_md, settings_yaml}` (settings — без секретов). |
| PUT | `/api/v1/clients/{id}/config/{file}` | `file` ∈ persona\|knowledge\|playbook\|settings. Тело `{content}`. Прогон через `loader.py`-валидацию; ошибка → `{error}` с тем же текстом, что у раннера. Успех → снапшот `.versions/` + `/reload`. |
| GET | `/api/v1/clients/{id}/versions` | Список снимков `[{ts, changed_files}]`. |
| POST | `/api/v1/clients/{id}/versions/{ts}/restore` | Восстановить снимок (`restore`) + reload. |

---

## 5. Тумблеры (М4) — поверх `yaml_edit.py`

| Метод | Путь | Тело | Примечание |
|---|---|---|---|
| POST | `/api/v1/clients/{id}/toggles/honesty` | `{mode: honest\|free_owner_liability, confirm:true, client_name}` | free требует confirm + имя (как `/honesty ... confirm`). Дефолт honest. |
| POST | `/api/v1/clients/{id}/toggles/funnel_gate` | `{on: bool, confirm:true}` | on требует confirm; ответ включает `catchup_warning` (§4 catch-up-риск). |
| POST | `/api/v1/clients/{id}/toggles/pause` | `{scope: global\|contact, contact_ref?, until_s?}` | global = kill_switch; contact = mute. |
| POST | `/api/v1/clients/{id}/toggles/resume` | `{scope, contact_ref?}` | |
| PUT | `/api/v1/clients/{id}/allowlist` | `{allow:[...], deny:[...]}` | Пишет `telegram.allowlist/denylist` + reload. |

Каждый тумблер пишет тот же yaml + reload; поведение 1:1 с командами пульта.

---

## 6. Лента / inbox (М5)

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/api/v1/clients/{id}/feed` | Пагинированная лента: эскалации, takeover, деградации, bot_question. `[{event, contact_ref, summary, ts, actions:[...]}]`. |
| POST | `/api/v1/clients/{id}/feed/{contact_ref}/reply` | `{text}` → раннер шлёт лиду (= сообщение персоны), ставит takeover-паузу. |
| POST | `/api/v1/clients/{id}/feed/{contact_ref}/action` | `{action: resume\|snooze\|stop\|keep}` — те же callbacks, что в control-bot. |
| GET | `/api/v1/clients/{id}/dialog/{contact_ref}` | Полная переписка (аудируемый доступ к ПДн, P8) — только по явному действию. |

---

## 7. Real-time (SSE)

| Метод | Путь | Поток |
|---|---|---|
| GET | `/api/v1/clients/{id}/stream` | SSE: `status_changed`, `feed_item`, `escalation`, `degradation`. Один поток на клиента. |
| GET | `/api/v1/stream` | SSE-агрегат по всем клиентам оператора (для М1-обзора). |

---

## 8. Аналитика (М6)

| Метод | Путь | Ответ |
|---|---|---|
| GET | `/api/v1/clients/{id}/metrics?window=7d` | `{dialogs, leads, escalations, avg_response_s, funnel:{new,qualifying,hot,escalated,closed}, bot_questions, tokens_est, cost_est}`. |
| GET | `/api/v1/metrics/overview` | Кросс-клиентский обзор для владельца пульта. |

---

## 9. Событийная шина + адаптеры (§6 ARCHITECTURE)

**Контракт события** (то, что уходит во все адаптеры):
```
{
  "event": "lead.created | lead.qualified | lead.escalated | lead.status_changed | dialog.taken_over | dialog.resumed | bot_question.asked",
  "client_id": "...",
  "contact_ref": "...",
  "ts": 1750000000.0,
  "payload": { ... }   // event-specific
}
```

**Управление интеграциями:**
| Метод | Путь | Назначение |
|---|---|---|
| GET | `/api/v1/clients/{id}/integrations` | Список настроенных адаптеров. |
| POST | `/api/v1/clients/{id}/integrations` | `{type: webhook\|sheets\|amocrm\|csv, config}` (MVP: webhook, sheets). |
| DELETE | `/api/v1/clients/{id}/integrations/{int_id}` | Отключить. |
| POST | `/api/v1/clients/{id}/integrations/{int_id}/test` | Тестовое событие → проверка доставки. |

**Интерфейс адаптера (плагин):** `emit(event: Event) -> DeliveryResult`. Webhook: POST + HMAC-подпись (паттерн `N8N_JARVIS_WEBHOOK_SECRET`). Sheets: append строки (креды `GOOGLE_SERVICE_ACCOUNT_JSON`). amoCRM/Kommo/CSV — тот же интерфейс, реализация v2.

---

## 10. Аудит (§7)

| Метод | Путь | Ответ |
|---|---|---|
| GET | `/api/v1/audit?window=30d` | `[{user, action, target, detail, ts}]` — кто что переключил. Только для владельца пульта. |

---

## Заметки

- Все `contact_ref` — псевдонимизированные ссылки, не сырые Telegram-id в URL без нужды.
- start/stop/onboarding-connect — **асинхронные**: возвращают состояние-в-процессе, финал через SSE/поллинг `status`.
- Валидация конфига — единственный источник правды `loader.py` (не дублировать правила в дашборде).
- OpenAPI-схема генерируется FastAPI автоматически — этот черновик станет живым `/docs`.
