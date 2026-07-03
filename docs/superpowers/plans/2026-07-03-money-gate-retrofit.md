# Арка 1 — Money-gate retrofit (prerequisite для меню-арки) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Подключить дневной лимит и учёт трат ко всем платным путям, которые сейчас тратят деньги **в обход** money-системы (`restaurant_mode`, `party_mode`, `personal_mode`, `faceswap`+`enhance`, `train_lora`/`create_persona`), плюс добавить пре-гейт к частично-дырявым `me_swap_photo/video`. Это prerequisite: только после мерджа Арки 1 команды Группы B можно открывать friend'у (Арка 2 — меню).

**Architecture:** Один общий хелпер `guard_spend(user_id, username, est_usd, do_spend)` инкапсулирует канонический паттерн `face_swap_handler`: `check_limit` СТРОГО до платного вызова (over-limit → 🚫, ничего не тратится и не пишется), `record_cost` ТОЛЬКО после успеха (провал/None → не платим). Каждый ungated spend-сайт оборачивается в хелпер на уровне хендлера (там есть `chat_id`=user_id). Сервисы генерации не трогаем внутри — гейтим на границе.

**Tech Stack:** Python 3, `app.services.auth.access_control.check_limit`, `app.services.audit.cost_tracker.record_cost`, pytest, monkeypatch-спаи.

---

## Что чиним (карта дыр — из чтения кода)

| Spend-сайт | Файл / точка | check_limit | record_cost | Класс |
|---|---|---|---|---|
| `generate_dish_photo` / `generate_social_post` / `generate_menu_series` | `photo_studio_telegram.py` handlers (124/147/184) | ❌ | ❌ | полная дыра |
| `generate_party_promo` / `generate_invite_card` / `generate_event_photo` | `photo_studio_telegram.py` (242/285/315) | ❌ | ❌ | полная дыра |
| me-creative `generate_me_as/in/in_style` (`generate_with_lora`) | `photo_studio_telegram.py` (786/812/838/864) → `personal_mode` | ❌ | ❌ | полная дыра |
| `enhance_face` (GFPGAN) | `photo_studio_telegram.py` (420) + faceswap-step (в `face_swap`) | ❌ | ❌ | полная дыра |
| faceswap swap (confirm callback) | `photo_studio_telegram.handle_faceswap_callback` | ❌ | ❌ | полная дыра |
| `/train_lora` (~$2.00), `/create_persona` seed-gen | `persona_handler.py` | ❌ | частично | полная/частичная |
| `cmd_smart_photo`, `cmd_pro_food` | `jarvis_smart_telegram_control.py` (6152/6156) | ❌ (проверить) | ❌ | дыра (локализовать) |
| `me_swap_photo` / `me_swap_video` | `persona_handler.py` (827/878) | ❌ | ✅ реальный `cost_usd` | **частичная** (пишет, но не гейтит) |

> **Эталон, который повторяем** (`face_swap_handler.py:523-544`): `est` = единый источник (quote==charge) → `check_limit(user_id, estimated_usd=est)` → при `not allowed` вернуть `🚫 {reason}` БЕЗ траты → платный вызов → при успехе `record_cost(user_id, username, est)`, при None/сбое НЕ платить. admin безлимитен, но `record_cost` пишется и ему (чинит неучёт трат в `/costs`).

## 📋 БЛОК ДЛЯ ДАНИИЛА — оценки стоимости (env-backed, финал за тобой)

Все `est` — через `os.getenv` с дефолтом (как `_envf("SWAPBATCH_VISION_PROMPT_USD", 0.01)`), чтобы менять без кода. Дефолты-черновики:

| Константа (env) | Дефолт | Команды |
|---|---|---|
| `PHOTO_DISH_USD` | 0.04 | /menu_photo, /social_post, /pro_food, /smart_photo |
| `PHOTO_MENU_BOOK_USD` (× N блюд) | 0.04 | /menu_book |
| `PHOTO_PARTY_USD` | 0.05 | /party_promo, /invite_card, /event_photo |
| `ME_CREATIVE_USD` | 0.04 | /me_as, /me_in, /me_with, /me_style, /me_into |
| `FACESWAP_USD` | 0.005 | /faceswap (в коде уже фигурирует $0.005) |
| `ENHANCE_USD` | 0.01 | /enhance |
| `TRAIN_LORA_USD` | 2.00 | /train_lora (в коде показывает ~$2.00) |
| `CREATE_PERSONA_SEED_USD` (× N сид-фото) | 0.04 | /create_persona |

> `me_swap_photo/video` НЕ в таблице: у них уже есть реальный `result["cost_usd"]` — там только добавляем пре-гейт `check_limit` на **оценку** до генерации, а `record_cost` остаётся по факту (не задваиваем).

---

## Файловая структура

- **Create:** `app/services/auth/spend_guard.py` — хелпер `guard_spend` (чистый, тестируемый, без Telegram).
- **Create:** `tests/test_spend_guard.py` — зубы хелпера.
- **Modify:** `tools/photo_studio_telegram.py` — обернуть spend-сайты restaurant/party/personal/faceswap/enhance.
- **Modify:** `app/handlers/persona_handler.py` — обернуть `train_lora`/`create_persona`; добавить пре-гейт в `me_swap_photo/video`.
- **Modify:** `tools/jarvis_smart_telegram_control.py` — обернуть `cmd_smart_photo`/`cmd_pro_food` (после локализации spend-точки).
- **Test:** `tests/test_money_gate_photo_studio.py`, `tests/test_money_gate_persona.py`.

---

## Task 1: Хелпер `guard_spend` (ядро, оба зуба)

**Files:**
- Create: `app/services/auth/spend_guard.py`
- Test: `tests/test_spend_guard.py`

- [ ] **Step 1: Failing tests — оба зуба + admin-леджер**

```python
# tests/test_spend_guard.py
import app.services.auth.spend_guard as sg

def _patch(monkeypatch, allowed, reason=""):
    calls = {"check": [], "record": [], "spend": 0}
    monkeypatch.setattr(sg, "check_limit", lambda uid, estimated_usd: (calls["check"].append((uid, estimated_usd)) or (allowed, reason)))
    monkeypatch.setattr(sg.cost_tracker, "record_cost", lambda uid, un, amt: calls["record"].append((uid, un, amt)))
    return calls

def test_over_limit_blocks_before_spend(monkeypatch):
    calls = _patch(monkeypatch, allowed=False, reason="лимит $1/день исчерпан")
    def do():  # must NEVER run
        calls["spend"] += 1
        return "img"
    result, err = sg.guard_spend(42, None, 0.04, do)
    assert result is None and "лимит" in err
    assert calls["spend"] == 0            # ЗУБ 1: трата без гейта невозможна
    assert calls["record"] == []          # ничего не списано

def test_success_records_ledger(monkeypatch):
    calls = _patch(monkeypatch, allowed=True)
    result, err = sg.guard_spend(42, None, 0.04, lambda: "http://img")
    assert result == "http://img" and err is None
    assert calls["record"] == [(42, None, 0.04)]   # ЗУБ 2a: успех → леджер

def test_failure_does_not_record(monkeypatch):
    calls = _patch(monkeypatch, allowed=True)
    result, err = sg.guard_spend(42, None, 0.04, lambda: None)  # сервис вернул None
    assert result is None and err is None
    assert calls["record"] == []                    # ЗУБ 2b: провал → НЕ платим

def test_admin_unlimited_still_records(monkeypatch):
    calls = _patch(monkeypatch, allowed=True)       # admin всегда allowed
    sg.guard_spend(999, "admin", 0.04, lambda: "ok")
    assert calls["record"] == [(999, "admin", 0.04)]  # чинит неучёт admin в /costs
```

- [ ] **Step 2: Run — verify fails** → FAIL (`No module named ...spend_guard`).

- [ ] **Step 3: Implement**

```python
# app/services/auth/spend_guard.py
import logging
from app.services.auth.access_control import check_limit
from app.services.audit import cost_tracker

logger = logging.getLogger(__name__)

def guard_spend(user_id, username, estimated_usd, do_spend):
    """Гейтит платную операцию каноническим паттерном.
    check_limit ДО траты; do_spend() выполняется только если allowed;
    record_cost ТОЛЬКО при truthy-результате. Возврат: (result, error_reason).
    error_reason != None → заблокировано лимитом (ничего не потрачено)."""
    allowed, reason = check_limit(user_id, estimated_usd=estimated_usd)
    if not allowed:
        return None, reason
    result = do_spend()
    if result:
        try:
            cost_tracker.record_cost(user_id, username, estimated_usd)
        except Exception as exc:  # noqa: BLE001 — учёт не должен ронять доставку
            logger.warning("guard_spend: record_cost failed: %s", exc)
    return result, None
```

- [ ] **Step 4: Run — verify passes** → PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat(money): guard_spend helper (limit gate + ledger, canonical pattern)"`

---

## Task 2: Гейт restaurant_mode (menu_photo / social_post / menu_book)

**Files:**
- Modify: `tools/photo_studio_telegram.py` (`handle_menu_photo` 102, `handle_social_post` 134, `handle_menu_book` 167)
- Test: `tests/test_money_gate_photo_studio.py`

- [ ] **Step 1: Failing test (зубы через спай)**

```python
# tests/test_money_gate_photo_studio.py
import tools.photo_studio_telegram as ps

def test_menu_photo_blocked_over_limit_never_generates(monkeypatch):
    gen = {"n": 0}
    monkeypatch.setattr(ps, "guard_spend",
        lambda uid, un, est, do: (None, "лимит исчерпан"))   # эмулируем over-limit
    monkeypatch.setattr("app.services.restaurant_mode.generate_dish_photo",
        lambda *a, **k: gen.__setitem__("n", gen["n"] + 1) or "url")
    sent = []
    ps.handle_menu_photo("42", "паста", lambda cid, t, **k: sent.append(t), lambda *a, **k: None)
    assert gen["n"] == 0                        # ЗУБ 1: генерации не было
    assert any("🚫" in s or "лимит" in s for s in sent)

def test_menu_photo_success_path_calls_guard(monkeypatch):
    seen = {}
    monkeypatch.setattr(ps, "guard_spend",
        lambda uid, un, est, do: seen.update(uid=uid, est=est) or (do(), None))
    monkeypatch.setattr("app.services.restaurant_mode.generate_dish_photo", lambda *a, **k: "http://img")
    photos = []
    ps.handle_menu_photo("42", "паста", lambda *a, **k: None, lambda cid, url, **k: photos.append(url))
    assert seen["uid"] == "42" and seen["est"] > 0
    assert photos == ["http://img"]
```

- [ ] **Step 2: Run — verify fails** → FAIL (`guard_spend` не импортирован / трата не гейтится).

- [ ] **Step 3: Implement** — в шапке `photo_studio_telegram.py` добавить `from app.services.auth.spend_guard import guard_spend` и `import os`. Каждый spend-хендлер по шаблону (пример `handle_menu_photo`):

```python
def handle_menu_photo(chat_id, query, send_fn, send_photo_fn):
    dish, style = _parse_menu_photo(query)   # существующий парс
    est = float(os.getenv("PHOTO_DISH_USD", "0.04"))
    def _do():
        from app.services.restaurant_mode import generate_dish_photo
        return generate_dish_photo(dish, style)
    url, err = guard_spend(chat_id, None, est, _do)
    if err:
        send_fn(chat_id, f"🚫 {err}")
        return
    if not url:
        send_fn(chat_id, "⚠️ Не удалось сгенерировать фото.")
        return
    send_photo_fn(chat_id, url)
```
  Аналогично `handle_social_post` (`generate_social_post`, est=`PHOTO_DISH_USD`) и `handle_menu_book` (est=`PHOTO_MENU_BOOK_USD` × число блюд; гейт на суммарную оценку ДО серии).

- [ ] **Step 4: Run — verify passes** → PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat(money): gate restaurant_mode photo commands"`

---

## Task 3: Гейт party_mode (party_promo / invite_card / event_photo)

**Files:** Modify `tools/photo_studio_telegram.py` (221/255/302); Test `tests/test_money_gate_photo_studio.py`

- [ ] **Step 1: Failing test** — по образцу Task 2 для `handle_party_promo`:

```python
def test_party_promo_blocked_over_limit(monkeypatch):
    gen = {"n": 0}
    monkeypatch.setattr(ps, "guard_spend", lambda *a: (None, "лимит"))
    monkeypatch.setattr("app.services.party_mode.generate_party_promo",
        lambda *a, **k: gen.__setitem__("n", gen["n"]+1) or {"url": "x"})
    sent = []
    ps.handle_party_promo("42", "неон", "", lambda cid, t, **k: sent.append(t), lambda *a, **k: None)
    assert gen["n"] == 0 and any("🚫" in s or "лимит" in s for s in sent)
```

- [ ] **Step 2: Run — verify fails** → FAIL.
- [ ] **Step 3: Implement** — обернуть `generate_party_promo`/`generate_invite_card`/`generate_event_photo` в `guard_spend`, est=`float(os.getenv("PHOTO_PARTY_USD","0.05"))`. Обрати внимание: эти сервисы возвращают dict/структуру — «успех» = truthy результат с валидным url; для `guard_spend` вернуть сам результат, а `if not result` считать сбоем.
- [ ] **Step 4: Run — verify passes** → PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat(money): gate party_mode photo commands"`

---

## Task 4: Гейт personal_mode me-creative (me_as / me_in / me_with / me_style / me_into)

**Files:** Modify `tools/photo_studio_telegram.py` (`handle_me_as` 771, `handle_me_in` 797, `handle_me_with` 823, `handle_me_style` 849, `handle_me_into_start`/step); Test `tests/test_money_gate_photo_studio.py`

- [ ] **Step 1: Failing test** — для `handle_me_as`:

```python
def test_me_as_blocked_over_limit_no_generate(monkeypatch):
    gen = {"n": 0}
    monkeypatch.setattr(ps, "guard_spend", lambda *a: (None, "лимит"))
    monkeypatch.setattr("app.services.personal_mode.generate_me_as",
        lambda *a, **k: gen.__setitem__("n", gen["n"]+1) or {"url": "x"})
    sent = []
    ps.handle_me_as("42", "пилот", lambda cid, t, **k: sent.append(t), lambda *a, **k: None)
    assert gen["n"] == 0 and any("🚫" in s or "лимит" in s for s in sent)

def test_me_as_success_records(monkeypatch):
    rec = []
    monkeypatch.setattr(ps, "guard_spend",
        lambda uid, un, est, do: rec.append(est) or (do(), None))
    monkeypatch.setattr("app.services.personal_mode.generate_me_as", lambda *a, **k: {"url": "http://i"})
    ps.handle_me_as("42", "пилот", lambda *a, **k: None, lambda *a, **k: None)
    assert rec and rec[0] > 0
```

- [ ] **Step 2: Run — verify fails** → FAIL.
- [ ] **Step 3: Implement** — обернуть `generate_me_as/in/in_style` (и `me_into` step, где вызывается генерация) в `guard_spend`, est=`float(os.getenv("ME_CREATIVE_USD","0.04"))`. `me_with` и `me_style` идут через `generate_me_in`/`generate_me_in_style` — тот же гейт.
- [ ] **Step 4: Run — verify passes** → PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat(money): gate personal_mode me-creative commands"`

---

## Task 5: Гейт faceswap (swap-confirm) + enhance (GFPGAN)

**Files:** Modify `tools/photo_studio_telegram.py` (`handle_faceswap_callback` ~464 — точка реального свапа; `enhance_face` вызовы 420 и faceswap-step 44); Test `tests/test_money_gate_photo_studio.py`

- [ ] **Step 1: Failing test** — для enhance-пути:

```python
def test_enhance_blocked_over_limit_no_call(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(ps, "guard_spend", lambda *a: (None, "лимит"))
    monkeypatch.setattr("app.services.face_swap.enhance_face",
        lambda *a, **k: calls.__setitem__("n", calls["n"]+1) or "url")
    sent = []
    ps._run_enhance("42", "http://photo", lambda cid, t, **k: sent.append(t))  # хелпер-обёртка (см. impl)
    assert calls["n"] == 0 and any("🚫" in s or "лимит" in s for s in sent)
```

- [ ] **Step 2: Run — verify fails** → FAIL.
- [ ] **Step 3: Implement** — выделить платный вызов `enhance_face`/свапа в маленький хелпер, обёрнутый `guard_spend` (est=`ENHANCE_USD` / `FACESWAP_USD`). Для faceswap реальный свап — в confirm-callback; гейтить там же перед вызовом свап-сервиса. `enhance_face` вызывается в двух местах (enhance-flow и faceswap-step) — DRY через один `_run_enhance`.
- [ ] **Step 4: Run — verify passes** → PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat(money): gate faceswap swap + GFPGAN enhance"`

---

## Task 6: Гейт train_lora (~$2) + create_persona seed-gen

**Files:** Modify `app/handlers/persona_handler.py` (`handle_train_lora` confirm→execute; `create_persona` seed-generation); Test `tests/test_money_gate_persona.py`

- [ ] **Step 1: Failing test** — train_lora не запускает тренировку при over-limit:

```python
# tests/test_money_gate_persona.py
import app.handlers.persona_handler as ph

def test_train_lora_blocked_over_limit_no_training(monkeypatch):
    started = {"n": 0}
    monkeypatch.setattr(ph, "guard_spend", lambda *a: (None, "лимит $1/день"))
    monkeypatch.setattr(ph, "_start_lora_training",
        lambda *a, **k: started.__setitem__("n", started["n"]+1))
    sent = []
    monkeypatch.setattr(ph, "_safe_send", lambda cid, t, **k: sent.append(t))
    ph._confirm_and_train(42, "persona_x")   # путь после «да» (см. impl)
    assert started["n"] == 0 and any("лимит" in s for s in sent)
```

- [ ] **Step 2: Run — verify fails** → FAIL.
- [ ] **Step 3: Implement** — на исполняющей ветке (ответ «да» в `_lora_pending_confirm`) обернуть запуск тренировки в `guard_spend(chat_id, None, TRAIN_LORA_USD, _start_training)`; при `err` — `🚫`, тренировка не стартует. Аналогично `create_persona` seed-генерацию гейтить (est=`CREATE_PERSONA_SEED_USD` × N). Существующий `_record_user_cost` для фактических трат оставить, но не задваивать с `guard_spend`-оценкой — для train_lora est==charge, поэтому одна запись.
- [ ] **Step 4: Run — verify passes** → PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat(money): gate train_lora and create_persona seed generation"`

---

## Task 7: Пре-гейт me_swap_photo / me_swap_video (частичная дыра)

**Files:** Modify `app/handlers/persona_handler.py` (`handle_me_swap_photo` 827, `handle_me_swap_video` 878); Test `tests/test_money_gate_persona.py`

- [ ] **Step 1: Failing test** — над-лимитный me_swap не генерирует:

```python
def test_me_swap_photo_blocked_over_limit(monkeypatch):
    gen = {"n": 0}
    monkeypatch.setattr(ph, "check_limit", lambda uid, estimated_usd: (False, "лимит"))
    monkeypatch.setattr(ph, "_generate_me_swap",
        lambda *a, **k: gen.__setitem__("n", gen["n"]+1))   # реальная генерация
    sent = []
    monkeypatch.setattr(ph, "_safe_send", lambda cid, t, **k: sent.append(t))
    ph.handle_me_swap_photo(42, "на пляже")
    assert gen["n"] == 0 and any("лимит" in s for s in sent)
```

- [ ] **Step 2: Run — verify fails** → FAIL (сейчас гейта нет — генерация идёт).
- [ ] **Step 3: Implement** — добавить `check_limit(chat_id, estimated_usd=est)` СТРОГО до платной генерации в обоих `me_swap_*`; при `not allowed` — `🚫`, генерация не стартует. `est` — консервативная оценка (env `ME_SWAP_USD`, дефолт 0.04). **Существующий `_record_user_cost(chat_id, result["cost_usd"])` по факту НЕ трогаем** — он пишет реальную стоимость после успеха; не заменяем на оценку, не задваиваем.
- [ ] **Step 4: Run — verify passes** → PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat(money): add pre-spend limit gate to me_swap photo/video"`

---

## Task 8: Локализовать и загейтить smart_photo / pro_food

**Files:** Modify `tools/jarvis_smart_telegram_control.py` (`cmd_smart_photo`, `cmd_pro_food`); Test `tests/test_money_gate_photo_studio.py`

- [ ] **Step 1: Найти spend-точку** — прочитать `cmd_smart_photo`/`cmd_pro_food`, найти вызов генерации (вероятно `restaurant_mode`/`replicate_image_gen`). Если платный и негейченный — обернуть в `guard_spend` (est=`PHOTO_DISH_USD`). Если уже гейчен (напр. идёт через `run_intent`/`generate`) — зафиксировать это тестом и пропустить.
- [ ] **Step 2: Failing test** (если негейчен) — по образцу Task 2.
- [ ] **Step 3: Implement** — обёртка `guard_spend` на найденной точке.
- [ ] **Step 4: Run — verify passes** → PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat(money): gate smart_photo/pro_food spend"`

> Если по факту `cmd_smart_photo`/`cmd_pro_food` идут через уже-гейченный общий генератор — тест это доказывает, кода-правки нет. Не гейтить дважды.

---

## Task 9: Регресс + живой тест

- [ ] Baseline тестов ДО Арки 1 зафиксирован; `pytest tests/ -q` — новых падений 0 (31 пре-существующее — техдолг, не наши).
- [ ] Все новые money-gate тесты зелёные.
- [ ] Рестарт бота (PYTHONUTF8=1).
- [ ] **Живой money-safe тест (реальные центы, малый лимит):** временно выставить friend-лимит низким, вызвать `/menu_photo` / `/me_as` / `/faceswap` дважды — второй раз должен упереться в `🚫 лимит`, генерации нет, `/my_stats` и `/costs` показывают списание только за успешный первый.
- [ ] Проверить `/costs` для admin: после `/menu_photo` трата **появляется** в леджере (раньше не учитывалась) — подтверждает фикс неучёта.
- [ ] Мердж Арки 1 → это разблокирует Группу B в Арке 2.

---

## Реализация T6 (вариант B, выбран Daniil)

Пре-гейт `check_limit` СТРОГО до старта (train_lora est=`TRAIN_LORA_USD` 2.00; create_persona est=`CREATE_PERSONA_SEED_USD`×20). Запись — **ФАКТИЧЕСКОЙ** стоимости на успешном завершении через новый колбэк `on_success_cost` (образец `persona_photo:518`): в `LoRATrainer._run_training` (реальный `result["cost_usd"]`) и в `PersonaCreator.generate_seed_photos` (сумма по успешным фото под `if urls`). Провал/прерывание → колбэк не вызывается → не списываем.

> **Известное ограничение (факт из кода, НЕ чиним):** тренировка/seed-ген идут в **daemon-потоке** (`start_training` спавнит `threading.Thread(..., daemon=True)`). Если бот рестартует посреди 15-25-мин тренировки, поток умирает → ветка успеха `_run_training` (где `on_success_cost` пишет в friend-леджер) и `log_expense` block_m **не выполняются**, хотя Replicate уже списал ~$2 → эта трата остаётся невидимой обоим леджерам (пре-гейт уже прошёл, ничего не записав). Персистентности/резюма прерванной тренировки нет (VideoQueue помечает job RUNNING, но воркер не переподхватывает). Помечено как известное ограничение — отдельный техдолг, в области Арки 1 не чиним.

## Риски

1. **Задвоение записи стоимости.** `me_swap_*` и persona-пути уже пишут реальный `cost_usd`. Для них добавляем ТОЛЬКО пре-гейт `check_limit`, `record_cost` не дублируем (Task 7 явно это фиксирует). Для полностью-дырявых — `guard_spend` пишет оценку (est==charge).
2. **Многошаговые флоу (faceswap, enhance, me_into).** Трата не в первом хендлере, а в шаге/конфирме. Гейт ставим на РЕАЛЬНОЙ точке вызова платного сервиса, не на старте флоу (иначе гейт мимо траты). Task 5 это учитывает.
3. **Оценка ≠ факт для image-gen.** FLUX pro/ultra и серии дают разную цену; est-дефолты в блоке Даниила — консервативные, quote==charge на оценке. Реальная сверка — задача биллинга, не блокер гейта.
4. **`generate_*` возвращают dict, а не url.** «Успех» для `guard_spend` = truthy результат; убедиться, что пустой/ошибочный dict не считается успехом (иначе спишем за сбой). Тесты Task 3 это ловят.
5. **Пре-существующие падения (31).** Baseline фиксируем; «зелёно» = не добавили новых.

## Что НЕ трогаем

- Внутренности сервисов генерации (`restaurant_mode`/`party_mode`/`personal_mode`/`replicate_image_gen`) — гейтим на границе хендлера, сервисы без изменений.
- Уже-гейченные пути (swap-batch, animate, videoref, persona_photo/video, vizir /task) — не трогаем.
- Реальный `cost_usd`-учёт в `me_swap_*` — сохраняем как есть, только добавляем гейт.
- Меню (Арка 2) — отдельная спека, начинается ПОСЛЕ мерджа Арки 1.
- Никаких новых зависимостей.

## Self-review (проведён)

- **Покрытие:** все ungated spend-сайты из карты дыр имеют задачу (T2 restaurant, T3 party, T4 personal, T5 faceswap+enhance, T6 train_lora+create_persona, T7 me_swap частичная, T8 smart/pro). ✔
- **Оба зуба пользователя:** «трата без гейта невозможна» (T1 test_over_limit_blocks + per-subsystem no-generate tests) и «успех→леджер, провал→нет» (T1 test_success/test_failure). ✔
- **Фикс /costs:** T1 test_admin_unlimited_still_records + T9 живой чек admin-леджера. ✔
- **Placeholder-скан:** код в шагах приведён; T8 намеренно discovery-first (честно помечено), не заглушка. ✔
- **Согласованность:** `guard_spend(user_id, username, estimated_usd, do_spend) -> (result, error)` — единая сигнатура во всех задачах; imports консистентны. ✔
