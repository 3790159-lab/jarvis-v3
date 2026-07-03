# Единое меню команд бота — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Дать пользователю (admin и friend) навигацию по ~120 командам через два слоя — нативное ☰ Telegram (`setMyCommands`, топ ~10-15 команд, разные списки по роли) и inline-каталог `/menu` (категории → пункты, навигация редактированием одного сообщения).

**Architecture:** Новый чистый модуль `tools/jarvis_menu.py` держит декларативный реестр меню (категории/пункты/роли/подписи/поведение) + чистые функции рендера и построения нативных payload'ов — без сети, полностью тестируемо. Тонкая обвязка в `tools/jarvis_smart_telegram_control.py`: команда `/menu`, ветка callback `menu:`, вызов `register_native_commands()` на старте. **Существующие команды не трогаются** — меню только вызывает их через существующий `handle()`.

**Tech Stack:** Python 3, Telegram Bot API (`setMyCommands`, `editMessageText`), pytest. Кастомный диспатч бота (не PTB).

---

## Friend-видимость (зафиксировано, ревизия 2)

Артём видит **те же категории, что admin, КРОМЕ**: ⚙️ Система, 🤖 Агенты, 🏗 Приложения и дизайн. 📊 Статистика для friend = только `/my_stats`.

Итого friend-меню: **🎬 Видео** (+ `/me_swap_photo`, `/me_swap_video`), **🎭 Персона** (полностью, вкл. LoRA), **🧑 Me-режимы** (полностью), **🍽 Photo Studio** (полностью), **📊 /my_stats**.

> ⛔ **PREREQUISITE — Арка 1 (money-gate retrofit).** Категории Photo Studio, Me-creative и `/train_lora`/`/create_persona` тратят деньги через `restaurant_mode`/`party_mode`/`personal_mode`, у которых **НЕТ** `check_limit`/`record_cost` (проверено grep'ом). Эти команды входят в friend (Группа B в diff ниже) **только после мерджа Арки 1** — см. `docs/superpowers/plans/2026-07-03-money-gate-retrofit.md`. Решение пользователя: **сначала гейт (Арка 1), потом доступ (эта Арка 2).**

## Ключевые архитектурные решения (зафиксированы)

1. **Два слоя:** нативное ☰ (быстрый доступ к топу) + `/menu` inline-каталог (полный каталог с подменю).
2. **Фильтрация по роли на этапе РЕНДЕРА** (`_role_for_chat`) — admin-пункты физически не попадают в friend-клавиатуру. Это **первый зуб** изоляции.
3. **Второй зуб:** exec-пункты меню исполняются через существующий `handle(chat_id, "/cmd")`, который повторно прогоняет role-гейт `FRIEND_ALLOWED_COMMANDS` (строка 6899-6904). Меню НЕ вызывает `handle_command` напрямую — иначе гейт обходится.
4. **Третий зуб:** `lookup_item(cmd, role)` — role-checked; friend-запрос admin-команды → `None` → «🚫».
5. **Навигация** редактированием одного сообщения (`editMessageText` по `message_id` из callback), префикс `menu:`.
6. Внутренние шаги флоу (`/swapbatch_confirm`, `/swapbatch_retry`, `/swapbatch_apply_*`, `/swapbatch_no`, `/me_seed`, `/me_done`) в меню **не кладём**.

## Callback-протокол (префикс `menu:`)

| callback_data | Действие |
|---|---|
| `menu:root` | Показать список категорий (фильтр по роли). Кнопка «⬅️ Назад» ведёт сюда. |
| `menu:cat:<cat_id>` | Показать пункты категории (фильтр по роли). |
| `menu:x:<cmd>` | Тап по пункту. Хендлер ищет пункт по `cmd` (role-checked); `exec` → `handle(chat_id, "/"+cmd)`; `hint` → `send(chat_id, item.hint)`. |

Длина: самый длинный `menu:x:swapbatch_animate_custom` = 31 байт < 64. Безопасно. Разделитель `:`; в именах команд `:` нет.

---

## Файловая структура

- **Create:** `tools/jarvis_menu.py` — реестр + чистые функции (нет сети, нет импорта основного файла).
  - `MENU: list[Category]` — упорядоченный реестр.
  - `render_root(role) -> tuple[str, list]` — (текст, inline_keyboard) корня.
  - `render_category(cat_id, role) -> tuple[str, list] | None` — пункты категории или None если категория недоступна роли.
  - `lookup_item(cmd, role) -> MenuItem | None` — role-checked поиск пункта.
  - `NATIVE_ADMIN_COMMANDS`, `NATIVE_FRIEND_COMMANDS: list[tuple[str, str]]` — (имя_без_слеша, описание) для нативного меню.
  - `build_native_payloads() -> dict` — payload'ы для `setMyCommands` (default=friend, admin-scope).
- **Modify:** `tools/jarvis_smart_telegram_control.py`
  - `handle_command` (5858): добавить ветку `if cmd == "/menu":` → `send_with_keyboard(render_root(role))`.
  - `handle_callback_query` (3939): добавить ветку `if data.startswith("menu:")` (см. Task 6).
  - `FRIEND_ALLOWED_COMMANDS` (7036): добавить `"/menu"`.
  - `FRIEND_ALLOWED_CALLBACK_PREFIXES` (7052): добавить `"menu:"`.
  - `main()` (8037): вызвать `register_native_commands()` до старта long-poll.
  - Новый хелпер `edit_message(chat_id, message_id, text, inline_keyboard)` (рядом с `send_with_keyboard`, 258).
- **Test:** `tests/test_jarvis_menu.py` (модуль), `tests/test_menu_bot_wiring.py` (обвязка, по образцу `tests/test_vizir_task_bot_wiring.py`).

---

## Модель данных (реестр)

```python
# tools/jarvis_menu.py
from dataclasses import dataclass
from typing import Literal, Optional

Role = Literal["admin", "friend"]
Action = Literal["exec", "hint"]

@dataclass(frozen=True)
class MenuItem:
    cmd: str                 # каноническая команда со слешем, напр. "/swapbatch_go"
    label: Optional[str]     # подпись 3-5 слов; None → в кнопке голая команда
    action: Action           # "exec" (сразу handle) | "hint" (показать подсказку)
    friend: bool             # виден ли роли friend
    hint: str = ""           # текст подсказки для action=="hint"

@dataclass(frozen=True)
class Category:
    cat_id: str              # короткий slug для callback, напр. "video"
    title: str               # "🎬 Видео и анимация"
    items: tuple             # tuple[MenuItem, ...]

def _visible(items, role):
    return [it for it in items if role == "admin" or it.friend]

def _btn_text(it):
    return f"{it.cmd} — {it.label}" if it.label else it.cmd
```

Правило видимости категории: категория видна роли, если у неё есть ≥1 видимый роли пункт (`_visible(cat.items, role)` не пуст).

---

## 📋 БЛОК ДЛЯ ДАНИИЛА №1 — Черновик подписей (финал редактируешь ты)

Правила из решения: подписи **обязательны** для Видео / Персона / Me / Photo Studio. Категории Система / Агенты / Статистика / Приложения — **голая команда** (label=None). `A`=exec (сразу выполнить), `H`=hint (подсказка). `F`=виден friend.

### 🎬 Видео и анимация (`video`) — подписи ОБЯЗАТЕЛЬНЫ
| Команда | Подпись (черновик) | Тап | Friend |
|---|---|---|---|
| /swapbatch | открыть свап-батч ≤100 фото | H | F |
| /swapbatch_source | задать исходное лицо | H | F |
| /swapbatch_batch | загрузить пачку фото | H | F |
| /swapbatch_go | запустить свап | A | F |
| /swapbatch_set_quality | качество: длительность/fps | H | F |
| /swapbatch_set_prompt | промт движения для видео | H | F |
| /swapbatch_set_wardrobe | одежда preserve/safe/spicy | H | F |
| /swapbatch_animate_go | анимировать swapped-фото | A | F |
| /swapbatch_animate_custom | свой промт на каждое фото | H | F |
| /swapbatch_status | статус батча | A | F |
| /swapbatch_cancel | отменить батч | A | F |
| /animate | анимировать одно фото | H | F |
| /animate_batch | анимировать пачку готовых фото | H | F |
| /animate_batch_go | запустить анимацию пачки | A | F |
| /videoref | референс-видео → свап+анимация | H | F |

### 🎭 Персона (`persona`) — подписи ОБЯЗАТЕЛЬНЫ
| Команда | Подпись (черновик) | Тап | Friend |
|---|---|---|---|
| /persona_photo | фото по обученной LoRA | H | F |
| /persona_video | фото + видео по LoRA | H | F |
| /persona_video_redo | переделать видео персоны | H | F |
| /persona_redo | переделать прошлую генерацию | H | F |
| /persona_engine | движок видео kling/wan22 | H | F |
| /persona_batch | N фото пачкой | H | F |
| /create_persona | создать новую персону | H | — |
| /cancel_persona | отменить создание персоны | A | — |
| /train_lora | обучить LoRA персоны | H | — |
| /lora_status | статус обучения LoRA | H | — |
| /list_loras | список готовых LoRA | A | — |
| /cancel_lora | отменить обучение LoRA | H | — |

### 🧑 Me-режимы (`me`) — подписи ОБЯЗАТЕЛЬНЫ · **friend-категория (после Арки 1)**
> Все пункты `friend=True`. `/me_swap_photo`, `/me_swap_video` уже friend; остальные (Группа B) — friend после мерджа Арки 1.
| Команда | Подпись (черновик) | Тап | Friend |
|---|---|---|---|
| /me_swap_photo | я в фото по промту | H | — |
| /me_swap_video | вставить моё лицо в видео | H | — |
| /me_into | вставить меня в чужое фото | H | — |
| /me_as | я в роли <роль> | H | — |
| /me_in | я в месте <место> | H | — |
| /me_with | я с предметом <предмет> | H | — |
| /me_style | я в стиле <стиль> | H | — |
| /me_roles | список ролей | A | — |
| /me_places | список мест | A | — |
| /me_styles | список стилей | A | — |

### 🍽 Photo Studio (`photo`) — подписи ОБЯЗАТЕЛЬНЫ · **friend-категория (после Арки 1)**
> Все пункты `friend=True` после мерджа Арки 1 (все тратят деньги → требуют гейта). Списки `/dish_styles`, `/party_themes` — бесплатны (Группа A, friend сразу).
| Команда | Подпись (черновик) | Тап |
|---|---|---|
| /menu_photo | фото блюда для меню | H |
| /social_post | пост для соцсетей | H |
| /menu_book | меню-книга из блюд | H |
| /dish_styles | стили подачи блюд | A |
| /pro_food | профи фуд-фото | H |
| /smart_photo | умное фото по описанию | H |
| /party_promo | промо вечеринки | H |
| /invite_card | пригласительная открытка | H |
| /event_photo | фото события | H |
| /party_themes | темы вечеринок | A |
| /faceswap | одиночный свап лица | A |
| /enhance | улучшить фото | A |

### 🏗 Приложения и дизайн (`apps`) — БЕЗ подписей (голые команды) · admin-only
Пункты (label=None): `/create_app` H, `/create_simple` H, `/simple_game` H, `/landing` H, `/landing_brief` H, `/landing_demo` A, `/bolt_status` A, `/bolt_open` A, `/bolt_queue` A, `/design` H, `/figma_queue` H, `/figma_status` A, `/figma_clear` A.

### 🤖 Агенты (`agents`) — БЕЗ подписей · admin-only
`/agents` A, `/mesh` H, `/cowork` H, `/plan` H, `/tasks` A, `/task` H, `/n8n` H, `/brain` H, `/research` H, `/engineer` H, `/table` H, `/gen` H, `/job` H.

### 📊 Статистика и деньги (`stats`) — БЕЗ подписей · friend видит только /my_stats
`/my_stats` A **F**, `/stats` A, `/costs` A, `/status` A, `/history` H, `/admin_users` A, `/admin_setlimit` H, `/admin_resetlimit` H, `/admin_activity` A.

### ⚙️ Система (`system`) — БЕЗ подписей · admin-only
`/smart_health` A, `/debug_health` A, `/diag` A, `/selfcheck` A, `/logs` H, `/errors` H, `/restart_backend` A, `/restart_bot` A, `/mode` H, `/cancel` A, `/clear` A, `/memory_stats` A, `/night_status` A, `/night_now` A, `/brief` H, `/remind` H, `/schedule` H, `/improve` H, `/capabilities` A.

---

## 📋 БЛОК ДЛЯ ДАНИИЛА №2 — Дубли и алиасы (какая каноническая в меню)

Из чтения кода — три класса. **Существующий код НЕ меняем**, меню лишь выбирает, что показать.

**A. Мёртвые тени (одно имя, второй обработчик недостижим — `if cmd==` возвращает на первом):**
| Команда | Живой (в меню) | Тень (dead, не показываем) |
|---|---|---|
| `/lora_status` | persona-версия (дисп. 6231) | photo-studio `_ps_cmd_map` (6593) — **затенена** |
| `/history` | общая история (дисп. 5924) | persona `/history` (6517) — **затенена** |
| `/cancel` | первая (дисп. 5968) | вторая (6168) — **затенена** |

**B. Похожие имена — РАЗНЫЕ подсистемы (обе живые, не настоящие алиасы):**
| Пара | Что каждая делает | В меню |
|---|---|---|
| `/train_lora` ↔ `/lora_train` | `/train_lora` — LoRA **персоны** (persona_handler); `/lora_train` — LoRA **Photo Studio** (photo_studio_telegram) | Обе в меню, но в разных категориях (Персона vs Photo Studio). НЕ схлопывать. |
| `/list_loras` ↔ `/lora_list` | `/list_loras` — персона-список; `/lora_list` — photo-studio-список | Показать по каноничной на подсистему. |

**C. Каноничный вход в свап-флоу:** friend'у при аппруве бот пишет `/swapbatch_source`, но точка входа — `/swapbatch`. В меню оставляем оба (это шаги одного флоу, не дубли). Даниил решает порядок.

> Действие Даниила: подтвердить каноничные, при желании пометить B-пары «показывать только одну».

---

## 📋 БЛОК ДЛЯ ДАНИИЛА №3 — Нативное меню ☰ (топ ~10-15, финал редактируешь ты)

Формат Telegram `BotCommand`: `command` — имя **без слеша**, ≤32 симв., lowercase; `description` ≤256 симв. Лимит — 100 команд на scope (укладываемся). Раскладка scope: **default = friend-список** (безопасный, его видят все, включая новых friend), **admin-scope = `BotCommandScopeChat(admin_id)`** поверх default.

### Admin ☰ (черновик, 15)
```
menu           — Каталог всех команд
status         — Статус AI-провайдеров
stats          — Статистика за сегодня
costs          — Траты за сегодня
smart_health   — Здоровье систем
swapbatch      — Пакетный свап лиц
animate        — Анимировать фото
videoref       — Референс-видео → свап
persona_photo  — Фото по LoRA-персоне
persona_video  — Видео по LoRA-персоне
agents         — Статус агентов
tasks          — Задачи Vizir
logs           — Читать логи
my_stats       — Личная статистика
help           — Справка
```

### Friend ☰ / default (черновик, 8) — только friend-команды
```
menu             — Меню команд
swapbatch_source — Задать исходное лицо
animate          — Анимировать фото
videoref         — Референс-видео → свап
persona_photo    — Фото по персоне
persona_video    — Видео по персоне
my_stats         — Моя статистика и лимит
help             — Справка
```

> **Зуб нативного слоя:** friend-список (default scope) НЕ содержит admin-команд; admin-команды видны только в admin-scope конкретного chat_id. Тест это проверяет (Task 4).

---

## 📋 БЛОК ДЛЯ ДАНИИЛА №4 — diff `FRIEND_ALLOWED_COMMANDS` (было 28 → станет 54)

Экспозиция ступенчатая: **вся Группа B добавляется только вместе с мерджем Арки 1** (money-gate). Группа A (бесплатные) технически безопасна и сразу, но по решению «две арки» весь diff применяется в Арке 2 после Арки 1.

**Остаётся без изменений (28, текущий набор):**
`/animate /animate_batch /animate_batch_go /swapbatch /swapbatch_source /swapbatch_batch /swapbatch_go /swapbatch_set_quality /swapbatch_set_prompt /swapbatch_set_wardrobe /swapbatch_animate_yes /swapbatch_animate_go /swapbatch_animate_no /swapbatch_animate_custom /swapbatch_status /swapbatch_cancel /persona_photo /persona_video /persona_video_redo /persona_redo /persona_engine /persona_batch /me_swap_photo /me_swap_video /videoref /my_stats /start /help`

**🟢 Группа A — бесплатные (списки/статус/отмена), +9:**
```
+ /cancel_persona   + /lora_status   + /list_loras   + /cancel_lora
+ /me_roles   + /me_places   + /me_styles   + /party_themes   + /dish_styles
```

**🔴 Группа B — платные, входят ТОЛЬКО после мерджа Арки 1, +17:**
```
+ /create_persona   + /train_lora
+ /me_into  + /me_as  + /me_in  + /me_with  + /me_style
+ /menu_photo  + /social_post  + /menu_book  + /pro_food  + /smart_photo
+ /party_promo  + /invite_card  + /event_photo
+ /faceswap  + /enhance
```

**Итог: 28 + 9 + 17 = 54.** Внутренние шаги флоу (`/swapbatch_confirm/retry/apply_*`, `/me_seed`, `/me_done`) в набор НЕ входят (в меню их нет). `/train_lora` в friend — намеренно (~$2, под дневным лимитом после Арки 1).

> **Зуб биллинга (Task в Арке 2 + гарантия Арки 1):** каждая команда Группы B к моменту добавления в этот список ДОЛЖНА проходить `check_limit` на своём spend-сайте (обеспечивает Арка 1). Меню-тест дополнительно проверяет: friend-exec через `handle()` упирается в `FRIEND_ALLOWED_COMMANDS`-гейт, а сама трата — в `check_limit`. Порядок мерджа: **Арка 1 → потом этот diff.**

---

## Задачи (TDD)

### Task 1: Реестр меню + фильтр по роли (чистый модуль)

**Files:**
- Create: `tools/jarvis_menu.py`
- Test: `tests/test_jarvis_menu.py`

- [ ] **Step 1: Failing test — реестр строится, роли фильтруются**

```python
# tests/test_jarvis_menu.py
import tools.jarvis_menu as m

def test_registry_has_expected_categories():
    ids = [c.cat_id for c in m.MENU]
    for expected in ("video", "persona", "me", "photo", "apps", "agents", "stats", "system"):
        assert expected in ids

def test_friend_sees_only_allowed_categories():
    friend_cats = [c.cat_id for c in m.MENU if m._visible(c.items, "friend")]
    # Расширено: friend видит те же, что admin, КРОМЕ system/agents/apps.
    assert set(friend_cats) == {"video", "persona", "me", "photo", "stats"}

def test_admin_only_items_never_visible_to_friend():
    for cat in m.MENU:
        for it in m._visible(cat.items, "friend"):
            assert it.friend is True  # зуб: ни один admin-пункт не просочился

def test_stats_category_friend_subset_is_only_my_stats():
    stats = next(c for c in m.MENU if c.cat_id == "stats")
    friend_items = [it.cmd for it in m._visible(stats.items, "friend")]
    assert friend_items == ["/my_stats"]
```

- [ ] **Step 2: Run — verify fails** — `pytest tests/test_jarvis_menu.py -v` → FAIL (`No module named tools.jarvis_menu`).

- [ ] **Step 3: Implement** — создать `tools/jarvis_menu.py` с dataclass'ами (см. «Модель данных») и полным реестром `MENU` из БЛОКА №1. Каждый `MenuItem(cmd=..., label=..., action=..., friend=..., hint=...)`. `friend=True` только на пунктах с пометкой **F**.

- [ ] **Step 4: Run — verify passes** — `pytest tests/test_jarvis_menu.py -v` → PASS.

- [ ] **Step 5: Commit** — `git add tools/jarvis_menu.py tests/test_jarvis_menu.py && git commit -m "feat(menu): declarative menu registry with role filter"`

---

### Task 2: Рендер корня и категории

**Files:**
- Modify: `tools/jarvis_menu.py`
- Test: `tests/test_jarvis_menu.py`

- [ ] **Step 1: Failing test**

```python
def test_render_root_lists_only_role_categories():
    text, kb = m.render_root("friend")
    datas = [b["callback_data"] for row in kb for b in row]
    assert "menu:cat:video" in datas
    assert "menu:cat:system" not in datas       # admin-only скрыта
    assert all(d.startswith("menu:cat:") for d in datas)

def test_render_category_friend_hides_admin_items():
    res = m.render_category("persona", "friend")
    assert res is not None
    _text, kb = res
    datas = [b["callback_data"] for row in kb for b in row]
    assert "menu:x:persona_photo" in datas       # friend-пункт есть
    assert "menu:x:train_lora" not in datas       # admin-пункт скрыт
    assert kb[-1][0]["callback_data"] == "menu:root"   # кнопка «⬅️ Назад»

def test_render_category_denied_for_role_returns_none():
    assert m.render_category("system", "friend") is None

def test_button_text_respects_label_presence():
    _t, kb = m.render_category("video", "admin")
    labels = [b["text"] for row in kb for b in row if b["callback_data"].startswith("menu:x:")]
    assert any(" — " in x for x in labels)        # Видео: подписи есть
    _t2, kb2 = m.render_category("system", "admin")
    sys_labels = [b["text"] for row in kb2 for b in row if b["callback_data"].startswith("menu:x:")]
    assert all(" — " not in x for x in sys_labels)  # Система: голые команды
```

- [ ] **Step 2: Run — verify fails** → FAIL (`render_root` не определён).

- [ ] **Step 3: Implement** в `jarvis_menu.py`:

```python
def render_root(role):
    rows = []
    for cat in MENU:
        if _visible(cat.items, role):
            rows.append([{"text": cat.title, "callback_data": f"menu:cat:{cat.cat_id}"}])
    text = "☰ Меню — выбери категорию:" if rows else "Нет доступных команд."
    return text, rows

def render_category(cat_id, role):
    cat = next((c for c in MENU if c.cat_id == cat_id), None)
    if cat is None:
        return None
    vis = _visible(cat.items, role)
    if not vis:
        return None
    rows = [[{"text": _btn_text(it), "callback_data": f"menu:x:{it.cmd.lstrip('/')}"}] for it in vis]
    rows.append([{"text": "⬅️ Назад", "callback_data": "menu:root"}])
    return f"{cat.title}", rows
```

- [ ] **Step 4: Run — verify passes** → PASS.

- [ ] **Step 5: Commit** — `git commit -am "feat(menu): render root and category keyboards"`

---

### Task 3: Role-checked `lookup_item`

**Files:** Modify `tools/jarvis_menu.py`; Test `tests/test_jarvis_menu.py`

- [ ] **Step 1: Failing test**

```python
def test_lookup_friend_of_admin_cmd_returns_none():
    assert m.lookup_item("train_lora", "friend") is None      # admin-only
def test_lookup_friend_of_friend_cmd_returns_item():
    it = m.lookup_item("persona_photo", "friend")
    assert it is not None and it.cmd == "/persona_photo"
def test_lookup_unknown_returns_none():
    assert m.lookup_item("nope_nope", "admin") is None
def test_lookup_admin_sees_admin_cmd():
    assert m.lookup_item("train_lora", "admin") is not None
```

- [ ] **Step 2: Run — verify fails** → FAIL.

- [ ] **Step 3: Implement**

```python
def lookup_item(cmd, role):
    norm = "/" + cmd.lstrip("/")
    for cat in MENU:
        for it in cat.items:
            if it.cmd == norm and (role == "admin" or it.friend):
                return it
    return None
```

- [ ] **Step 4: Run — verify passes** → PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat(menu): role-checked item lookup"`

---

### Task 4: Нативные payload'ы для `setMyCommands`

**Files:** Modify `tools/jarvis_menu.py`; Test `tests/test_jarvis_menu.py`

- [ ] **Step 1: Failing test**

```python
def test_native_commands_shape_valid():
    for name, desc in m.NATIVE_ADMIN_COMMANDS + m.NATIVE_FRIEND_COMMANDS:
        assert not name.startswith("/") and name == name.lower()
        assert 1 <= len(name) <= 32 and len(desc) <= 256

def test_friend_native_has_no_admin_commands():
    admin_only = {"status", "agents", "tasks", "logs", "restart_bot", "smart_health"}
    friend_names = {n for n, _ in m.NATIVE_FRIEND_COMMANDS}
    assert friend_names.isdisjoint(admin_only)      # зуб нативного слоя

def test_build_native_payloads_scopes():
    p = m.build_native_payloads(admin_chat_id="123")
    assert p["default"]["scope"]["type"] == "default"
    assert {c["command"] for c in p["default"]["commands"]} == {n for n, _ in m.NATIVE_FRIEND_COMMANDS}
    assert p["admin"]["scope"] == {"type": "chat", "chat_id": "123"}
    assert {c["command"] for c in p["admin"]["commands"]} == {n for n, _ in m.NATIVE_ADMIN_COMMANDS}
```

- [ ] **Step 2: Run — verify fails** → FAIL.

- [ ] **Step 3: Implement** — списки из БЛОКА №3 + сборка:

```python
NATIVE_ADMIN_COMMANDS = [("menu", "Каталог всех команд"), ("status", "Статус AI-провайдеров"), ...]
NATIVE_FRIEND_COMMANDS = [("menu", "Меню команд"), ("swapbatch_source", "Задать исходное лицо"), ...]

def _cmds(pairs):
    return [{"command": n, "description": d} for n, d in pairs]

def build_native_payloads(admin_chat_id):
    return {
        "default": {"commands": _cmds(NATIVE_FRIEND_COMMANDS), "scope": {"type": "default"}},
        "admin": {"commands": _cmds(NATIVE_ADMIN_COMMANDS), "scope": {"type": "chat", "chat_id": str(admin_chat_id)}},
    }
```

- [ ] **Step 4: Run — verify passes** → PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat(menu): native setMyCommands payloads per role"`

---

### Task 5: Обвязка команды `/menu` + разрешения

**Files:**
- Modify: `tools/jarvis_smart_telegram_control.py` (`handle_command` ~5858; `FRIEND_ALLOWED_COMMANDS` 7036; `FRIEND_ALLOWED_CALLBACK_PREFIXES` 7052)
- Test: `tests/test_menu_bot_wiring.py`

- [ ] **Step 1: Failing test** (по образцу `tests/test_vizir_task_bot_wiring.py` — импорт модуля, проверка констант)

```python
# tests/test_menu_bot_wiring.py
import importlib
mod = importlib.import_module("tools.jarvis_smart_telegram_control")

def test_menu_command_is_friend_allowed():
    assert "/menu" in mod.FRIEND_ALLOWED_COMMANDS

def test_menu_prefix_in_friend_callback_prefixes():
    assert "menu:" in mod.FRIEND_ALLOWED_CALLBACK_PREFIXES
```

- [ ] **Step 2: Run — verify fails** → FAIL.

- [ ] **Step 3: Implement** в основном файле:
  - В `FRIEND_ALLOWED_COMMANDS` (7036) добавить `"/menu"`.
  - В `FRIEND_ALLOWED_CALLBACK_PREFIXES` (7052) добавить `"menu:"`.
  - Импорт вверху: `from tools import jarvis_menu as jmenu` (или лениво в ветке, как делают другие блоки).
  - В `handle_command` добавить ветку (роль берём из `_role_for_chat`):

```python
    if cmd == "/menu":
        _mrole = _role_for_chat(chat_id) or ("admin" if str(chat_id) == ALLOWED_CHAT_ID else "friend")
        _mrole = "admin" if _mrole == "admin" else "friend"
        _text, _kb = jmenu.render_root(_mrole)
        send_with_keyboard(chat_id, _text, _kb)
        return
```

- [ ] **Step 4: Run — verify passes** → PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat(menu): wire /menu command and friend permissions"`

---

### Task 6: Ветка callback `menu:` + `edit_message` + exec через `handle()`

**Files:**
- Modify: `tools/jarvis_smart_telegram_control.py` (`handle_callback_query` 3939; новый хелпер `edit_message` рядом с 258)
- Test: `tests/test_menu_bot_wiring.py`

- [ ] **Step 1: Failing test** (мокаем I/O; проверяем маршрутизацию)

```python
def test_menu_exec_routes_through_handle(monkeypatch):
    calls = {}
    monkeypatch.setattr(mod, "handle", lambda cid, text: calls.setdefault("handle", (cid, text)))
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_role_for_chat", lambda cid: "friend")
    cq = {"id": "1", "from": {"id": 999}, "message": {"message_id": 5, "chat": {"id": 999}},
          "data": "menu:x:swapbatch_status"}
    mod.handle_callback_query(cq, {})
    assert calls["handle"] == ("999", "/swapbatch_status")   # exec → handle(), гейт применится

def test_menu_friend_blocked_from_admin_item(monkeypatch):
    sent = {}
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: sent.setdefault("ack", a))
    monkeypatch.setattr(mod, "handle", lambda *a, **k: sent.setdefault("handle", True))
    monkeypatch.setattr(mod, "_role_for_chat", lambda cid: "friend")
    cq = {"id": "1", "from": {"id": 999}, "message": {"message_id": 5, "chat": {"id": 999}},
          "data": "menu:x:train_lora"}
    mod.handle_callback_query(cq, {})
    assert "handle" not in sent                              # admin-команда не исполнена
```

> Примечание: friend-гейт callback (3940-3945) пропускает `menu:` уже после Task 5. Внутри ветки `menu:x:` третий зуб — `jmenu.lookup_item(cmd, role)`.

- [ ] **Step 2: Run — verify fails** → FAIL.

- [ ] **Step 3: Implement**
  - Хелпер рядом с `send_with_keyboard` (258):

```python
def edit_message(chat_id, message_id, text, inline_keyboard):
    tg_call("editMessageText", {
        "chat_id": chat_id, "message_id": message_id,
        "text": text[:3900],
        "reply_markup": {"inline_keyboard": inline_keyboard},
        "disable_web_page_preview": True,
    })
```

  - В `handle_callback_query`, внутри уже существующего блока (после friend-гейта, рядом с ветками `access:`/`vref:`), добавить:

```python
    if data.startswith("menu:"):
        _uid = (callback_query.get("from") or {}).get("id")
        role = _role_for_chat(_uid) or ("admin" if str(_uid) == ALLOWED_CHAT_ID else "friend")
        role = "admin" if role == "admin" else "friend"
        msg_id = (callback_query.get("message") or {}).get("message_id")
        if data == "menu:root":
            _t, _kb = jmenu.render_root(role)
            edit_message(chat_id, msg_id, _t, _kb)
            answer_callback_query(cq_id)
            return
        if data.startswith("menu:cat:"):
            res = jmenu.render_category(data.split(":", 2)[2], role)
            if res is None:
                answer_callback_query(cq_id, "🚫 Недоступно")
                return
            _t, _kb = res
            edit_message(chat_id, msg_id, _t, _kb)
            answer_callback_query(cq_id)
            return
        if data.startswith("menu:x:"):
            _cmd = data.split(":", 2)[2]
            item = jmenu.lookup_item(_cmd, role)      # третий зуб
            if item is None:
                answer_callback_query(cq_id, "🚫 Только для администратора")
                return
            if item.action == "exec":
                answer_callback_query(cq_id, "▶️")
                handle(str(chat_id), item.cmd)         # второй зуб: гейт FRIEND_ALLOWED_COMMANDS
            else:
                answer_callback_query(cq_id)
                send(chat_id, item.hint)
            return
```

- [ ] **Step 4: Run — verify passes** → PASS. Затем полная сюита: `pytest tests/ -q` (регресс не хуже baseline — см. Риски про 31 пре-существующее падение).
- [ ] **Step 5: Commit** — `git commit -am "feat(menu): callback navigation, edit-in-place, gated exec/hint"`

---

### Task 7: Регистрация нативного меню на старте

**Files:**
- Modify: `tools/jarvis_smart_telegram_control.py` (`main()` 8037, до long-poll)
- Test: `tests/test_menu_bot_wiring.py`

- [ ] **Step 1: Failing test**

```python
def test_register_native_commands_calls_set_my_commands(monkeypatch):
    seen = []
    monkeypatch.setattr(mod, "tg_call", lambda method, payload: seen.append((method, payload)) or {})
    monkeypatch.setattr(mod, "ALLOWED_CHAT_ID", "42", raising=False)
    mod.register_native_commands()
    methods = [m_ for m_, _ in seen]
    assert methods.count("setMyCommands") == 2                # default + admin scope
    scopes = [p["scope"]["type"] for m_, p in seen if m_ == "setMyCommands"]
    assert set(scopes) == {"default", "chat"}
```

- [ ] **Step 2: Run — verify fails** → FAIL.

- [ ] **Step 3: Implement**

```python
def register_native_commands():
    try:
        payloads = jmenu.build_native_payloads(admin_chat_id=ALLOWED_CHAT_ID)
        for key in ("default", "admin"):
            tg_call("setMyCommands", payloads[key])
        print("[menu] native commands registered", flush=True)
    except Exception as e:  # регистрация меню не должна ронять бот
        print(f"[menu] setMyCommands failed: {e}", flush=True)
```
  Вызвать `register_native_commands()` в `main()` один раз до цикла `getUpdates` (8130).

- [ ] **Step 4: Run — verify passes** → PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat(menu): register native setMyCommands on startup"`

---

### Task 8: Живой тест (после рестарта бота)

- [ ] Рестарт бота (PYTHONUTF8=1, см. память про detached UTF-8).
- [ ] Admin: `/menu` → категории → тап категория (сообщение **редактируется**, не плодится) → «⬅️ Назад» → exec-пункт (`/status`) выполняется → hint-пункт показывает подсказку.
- [ ] Admin: открыть нативное ☰ → виден admin-топ.
- [ ] Friend (Артём, с телефона): нативное ☰ → **только** friend-список, admin-команд нет. `/menu` → только 🎬/🎭/📊; в 📊 только `/my_stats`. Тап admin-глубины недостижим (категорий нет). exec friend-пункта работает, деньги-гейт цел.
- [ ] Проверить, что ни одна существующая команда не сломалась (spot-check `/swapbatch_source`, `/animate`).

---

## Риски

1. **Обход role-гейта при exec.** Меню обязано звать `handle(chat_id, "/cmd")`, НЕ `handle_command` напрямую — гейт `FRIEND_ALLOWED_COMMANDS` живёт в `handle()` (6899). Тест Task 6 фиксирует именно `handle`. Если рефакторить — не потерять этот зуб.
2. **Рассинхрон реестра и реальных команд.** Пункт меню ссылается на несуществующую/переименованную команду → «Не знаю такую команду». Митигейт: реестр — единственный источник; при изменении команд обновлять `jarvis_menu.py`. (Опционально позже: тест сверки cmd реестра со списком `if cmd==` — сейчас НЕ делаем, скоуп.)
3. **`editMessageText` «message is not modified».** Тап той же категории повторно → Telegram 400. Не критично (ошибка глушится в `tg_call`), но если раздражает — добавить no-op при неизменности (позже).
4. **Мёртвые тени (Блок №2A).** Не чиним существующий код; просто не показываем затенённый вариант. Осознанный техдолг.
5. **Пре-существующие падения тестов.** В ветке ~31 не связанное с меню падение (runpod/pydantic/mobile_ux, см. память). Baseline фиксируем ДО Task 1; «зелёно» = не добавили новых падений, а не «0 падений».
6. **Нативное меню кэшируется клиентом Telegram.** После `setMyCommands` список у клиента может обновиться с задержкой/после реоткрытия чата. Для живого теста — переоткрыть диалог.
7. **Экспозиция Группы B БЕЗ Арки 1 = дыра в лимите.** Никогда не добавлять Группу B в `FRIEND_ALLOWED_COMMANDS` и не ставить `friend=True` на её пункты, пока Арка 1 (money-gate) не смерджена. Порядок жёсткий: Арка 1 → Арка 2-diff. Меню для admin можно показывать со всеми категориями сразу (admin безлимитен).

## Что НЕ трогаем

- **Существующие команды и их обработчики** — ноль правок логики; меню только вызывает через `handle()`.
- Диспатч `if cmd == "/..."`, мапы `mapped`/`_ps_cmd_map`.
- Мёртвые тени и дубли — не рефакторим (Блок №2).
- `classify_message`, интерцепторы (video_face_swap), money-гейты, Vizir/Hermes.
- 31 пре-существующее падение — отдельный техдолг.
- Никаких новых зависимостей.

---

## Self-review (проведён)

- **Покрытие решений:** два слоя (Task 5-7 + jmenu), категории admin/friend (Task 1-2), фильтр по роли на рендере (Task 1-2, зуб-тесты), подписи обязательные/голые (Task 2 тест `_btn_text`), черновик подписей (Блок №1), дубли (Блок №2), нативное per-scope (Task 4/7 + Блок №3), внутренние шаги исключены (реестр), exec/hint помечены (Блоки + Task 6), `menu:` в prefixes (Task 5), существующие команды не тронуты (раздел «Что НЕ трогаем»). ✔
- **Placeholder-скан:** код в шагах приведён; списки-заглушки в Task 4/Блок №3 помечены как «черновик для Даниила», не как код-заглушки. ✔
- **Согласованность типов:** `MenuItem(cmd,label,action,friend,hint)`, `Category(cat_id,title,items)`, `render_root/render_category/lookup_item/build_native_payloads`, callback `menu:root|cat:<id>|x:<cmd>` — имена совпадают во всех задачах. ✔
