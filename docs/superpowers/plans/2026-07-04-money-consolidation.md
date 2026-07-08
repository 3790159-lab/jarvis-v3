# Money-Consolidation (леджер-консолидация) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Закрыть три денежные дыры одного корня, сделав **audit-леджер единственным источником правды**: (a) добавить пре-гейт `check_limit` на 5 персона-генераций + `/me_done`, (b) выпилить дублирующий инертный block_m-бюджет, (c) минимально-честно закрыть потерю траты при рестарте посреди тренировки (резерв-запись на старте).

**Architecture:** Единый гейт = audit (`app/services/auth/access_control.check_limit` ДО платного вызова + `app/services/audit/cost_tracker.record_cost` факт-стоимости ПОСЛЕ успеха — паттерн Арки 1 T6). Пре-гейты берут оценку из env-переменных (как `TRAIN_LORA_USD` и пр. в Арке 1). block_m-бюджет (`DAILY_LIMIT_USD=10` глобальный, читает мёртвый `expenses.jsonl`, никогда не срабатывает) удаляется как redundant per-user friend-лимиту. Для (c) — на старте тренировки пишется резерв (оценка) в audit, на успехе — дельта (факт−оценка); полная сверка orphan-джобов = отдельный техдолг.

**Tech Stack:** Python 3.11, pytest, кастомный диспатч бота (не PTB). Тесты только на моках, ноль реальных Replicate/сетевых вызовов.

---

## Разведанное состояние (факты, `file:line`)

**Два леджера:**
- **AUDIT (жив)** `state/cost_tracking.json` — writer `app/services/audit/cost_tracker.py:93` `record_cost(user_id, username, amount_usd, ts=None)`; readers `get_user_stats:160`, `format_my_stats_message:238` (/my_stats), `format_admin_costs_message:258` (/costs). Пре-гейт `check_limit(user_id, *, estimated_usd)` — **отдельный модуль** `app/services/auth/access_control.py:17` (читает `get_user_stats()["today"]` + per-user лимит из `users_store`).
- **block_m (мёртвый/стагнирующий)** `state/personas/expenses.jsonl` — writer `log_expense` `app/services/block_m_common/cost_tracker.py:45`; бюджет-чек `check_limit`→`get_today_total`→`_read_all` (`block_m_common/cost_tracker.py:106,85,153`); константа `DAILY_LIMIT_USD=10.0` `:12`; исключение `DailyLimitExceeded` `:18`. Последняя запись 2026-05-06. Единственный богатый читатель `app/services/block_m23_polish/analytics.py` **НЕ подключён** ни к одной Telegram-команде.

**Дыра (a) — 5 живых персона-генераций БЕЗ пре-гейта** (только факт-запись на успехе):

| Команда | Хендлер (живой) | Платный вызов | Запись факта | Пре-гейт |
|---|---|---|---|---|
| `/persona_photo` | `persona_handler.py:517` `handle_persona_photo` | `photo_generator.py:71` | `_record_user_cost` `persona_handler.py:560` | **НЕТ** |
| `/persona_video` | `ctl:423` `_persona_video_dispatch`→`persona_video_handler.py:239` | `persona_video_handler.py:308` | `_cost.record_cost` `ctl:508` | **НЕТ** |
| `/persona_video_redo` | `persona_video_handler.py:323` `handle_redo` | `persona_video_handler.py:378` | `ctl:508` | **НЕТ** |
| `/persona_redo` | `persona_handler.py:642` | `persona_handler.py`→BatchGenerator | `_record_user_cost` `persona_handler.py:690` | **НЕТ** |
| `/persona_batch` | `persona_handler.py:1072` | `persona_handler.py:1110` `BatchGenerator.generate_batch` | `_record_user_cost` `persona_handler.py:1124` | **НЕТ** |
| `/me_done`→train | `persona_handler.py:826` `_do_train_me_lora` | `persona_handler.py:847` `train_flux_lora` | **НИ В ОДИН леджер** | **НЕТ** |

> ⚠️ Мёртвый код — НЕ трогать: `handle_persona_video` `persona_handler.py:570` НЕ диспатчится (живой путь `/persona_video` = `PersonaVideoHandler` через `ctl:423`). Гейтить `_persona_video_dispatch`, не `handle_persona_video`.
> Для контраста, УЖЕ с пре-гейтом (Арка 1): `create_persona` seed `persona_handler.py:277`, `/train_lora` `persona_handler.py:360`, `/me_swap_photo` `:877`, `/me_swap_video` `:934`.

**Дыра (b):** `lora_trainer.py:102-106` и `photo_generator.py:57-59` (+ `persona_creator.py:91` через `log_expense`) зовут block_m `check_limit()` → `DailyLimitExceeded`. Читают глобальный сумматор мёртвого `expenses.jsonl` → почти всегда ~0 → **никогда не срабатывает**. Бюджет **глобальный** (нет `user_id`), $10/день хардкод, дублирует реальный per-user `access_control.check_limit` (лимит `users_store.DEFAULT_FRIEND_LIMIT_USD=5.0`, живой audit). Никто не полагается.

**Дыра (c):** `LoRATrainer.start_training` `lora_trainer.py:64` пишет job-статус (`_save_jobs:121`) и запускает **daemon-поток** `_run_training` (`lora_trainer.py:129-137`, `daemon=True:137`). Платный `train_flux_lora` `lora_trainer.py:160`; запись стоимости ТОЛЬКО после: block_m `:171`, audit `on_success_cost` `:180-184` (провязано из `persona_handler.py:387`). **Резерва на старте НЕТ** — рестарт до `:171/:180` → daemon умирает → трата в оба леджера не попадает. То же для seed (`persona_creator.py:137`) и `_do_train_me_lora` (`persona_handler.py:835`, вдобавок вообще без записи).

**guard_spend** `app/services/auth/spend_guard.py:21` `guard_spend(user_id, username, estimated_usd, do_spend)->(result, err)` — check_limit ДО, record_cost **оценки** ПОСЛЕ успеха. ⚠️ Пишет ОЦЕНКУ, не факт. Персона-хендлеры уже пишут ФАКТ через `_record_user_cost` → для (a) `guard_spend` НЕ годится (двойная запись). Для (a) = добавить голый `check_limit` пре-гейт + сохранить существующую факт-запись.

---

## 📋 Допущения — подтвердить при ОК (разрешено самостоятельно, зафиксировано)

1. **Семантика (a): пре-гейт БЕЗ смены записи.** Добавляем `check_limit(chat_id, estimated_usd=EST)` перед платным вызовом; **существующую факт-запись на успехе не трогаем** (иначе двойное списание). Не используем `guard_spend` для (a) — он пишет оценку. Оценки — env-переменные (см. ниже).
2. **Оценки (env, дефолты — консервативная верхняя граница):** `PERSONA_PHOTO_USD=0.05`, `PERSONA_VIDEO_USD=0.40`, `PERSONA_REDO_USD=0.05`, `PERSONA_BATCH_USD` (per-photo) `=0.05` → est = per×N, `TRAIN_ME_LORA_USD=5.00`. **⚠️ Разночтение:** промпт назвал `/me_done` «~$2», но код квотирует **$5** (`persona_handler.py:821`). Взял $5 (факт кода). Подтвердить: $5 верна? если реальная тренировка дешевле — снизить env.
3. **(b) РЕШЕНИЕ = ВЫПИЛИТЬ block_m-бюджет** (не чинить на audit). Обоснование фактами: глобальный $10 хардкод, читает мёртвый леджер, никогда не срабатывает, дублирует per-user friend-лимит (реальную защиту даёт (a)), ни один живой потребитель не полагается. Удаляем ТОЛЬКО enforcement (вызовы `check_limit`+`raise DailyLimitExceeded` в 3 местах). `log_expense`-записи оставляем как безвредный вторичный след (полное удаление block_m-леджера = отдельный техдолг, вне области).
4. **(c) минимально-честно = резерв+дельта.** На старте тренировки пишем `record_cost(est)` (резерв). На успехе меняем `on_success_cost` писать **дельту** `(actual − est)` вместо полного факта (итог = est+дельта = actual). Рестарт до успеха → в леджере остаётся оценка (честно: заряд, вероятно, случился). **Полная сверка** (startup-скан orphan-джобов `lora_jobs.json` в статусе running + запрос фактической стоимости у Replicate + коррекция) = **отдельный техдолг**, помечен ниже.
5. **admin безлимитен, но пишется** (как везде в Арке 1): пре-гейт для admin проходит (`check_limit` unlimited), факт/резерв всё равно в леджере (видимость /costs).
6. **Порядок задач жёсткий:** (a) до (b) — сначала добавить реальную per-user защиту, потом убрать фейковую. (c) независимо, после.

---

## Файловая структура

> ⚠️ **ПУТИ СВЕРЕНЫ ФАКТОМ (разведка дала неверные префиксы каталогов):** `persona_handler.py` = **`app/handlers/persona_handler.py`** (НЕ `app/services/block_m/`); `lora_trainer.py`/`photo_generator.py`/`persona_creator.py` = **`app/services/block_m1_persona/`**; `cost_tracker` (block_m) = `app/services/block_m_common/cost_tracker.py`; audit = `app/services/audit/cost_tracker.py`; `check_limit` = `app/services/auth/access_control.py`. Номера строк внутри файлов — из разведки @da2312e, сверять по именам.

- **Modify** `app/handlers/persona_handler.py` — пре-гейты `handle_persona_photo`(517, сигнатура `(chat_id:int, args:str)`; платный `generate_photo` ~543, факт `_record_user_cost` ~560), `handle_persona_redo`(642), `handle_persona_batch`(1072), `_do_train_me_lora`(826)+факт-запись; резерв-провязка тренировок (T7).
- **Modify** `tools/jarvis_smart_telegram_control.py` — пре-гейт в `_persona_video_dispatch`(423) (покрывает `/persona_video` и `_redo`).
- **Modify** `app/services/block_m2_video/persona_video_handler.py` — если оценка нужна внутри (иначе гейт в ctl).
- **Modify** `app/services/block_m1_persona/lora_trainer.py` — убрать block_m budget enforcement (:102-106); резерв на старте + дельта on_success (T6/T7).
- **Modify** `app/services/block_m1_persona/photo_generator.py` — убрать block_m budget enforcement (:57-59).
- **Modify** `app/services/block_m1_persona/persona_creator.py` — резерв на старте seed + дельта (T7); block_m log_expense оставить.
- **Test:** `tests/test_persona_pregate.py` (a), `tests/test_block_m_budget_removed.py` (b), `tests/test_training_reservation.py` (c). Все на моках.

> ⚠️ Сигнатуры хендлеров (напр. `handle_persona_photo(chat_id:int, args:str)` — `send` модульный, не параметр; тесты в задачах упрощены, адаптировать под реальную сигнатуру), наличие `_record_user_cost`/`check_limit`-импорта сверить в исполнении. Импорт `check_limit`: `from app.services.auth.access_control import check_limit`.

---

## Задачи (TDD)

### Task 1: Пре-гейт `/persona_photo`

**Files:** Modify `app/handlers/persona_handler.py`; Test `tests/test_persona_pregate.py`

- [ ] **Step 1: Failing test** — спай на платный `PhotoGenerator.generate_photo` (мок) + мок `check_limit`. При `check_limit`→(False, "лимит") платный вызов НЕ зван, юзеру «🚫 …», факт НЕ записан. При (True) — платный зван, факт записан как раньше.

```python
# tests/test_persona_pregate.py
def test_persona_photo_pregate_blocks_before_paid_call(monkeypatch):
    import app.services.block_m.persona_handler as ph
    called = {}
    monkeypatch.setattr(ph, "check_limit", lambda uid, *, estimated_usd: (False, "дневной лимит $5"))
    monkeypatch.setattr(ph, "_record_user_cost", lambda *a, **k: called.setdefault("rec", True))
    # PhotoGenerator.generate_photo replaced to record if it was reached
    monkeypatch.setattr(ph.PhotoGenerator, "generate_photo",
                        lambda self, *a, **k: called.setdefault("paid", True) or {"cost_usd": 0.05})
    sent = []
    ph.handle_persona_photo("237616472", "...", send=lambda cid, t: sent.append(t))
    assert "paid" not in called          # платный НЕ зван
    assert "rec" not in called           # факт НЕ записан
    assert any("🚫" in s for s in sent)   # отказ показан
```

- [ ] **Step 2: Run — verify fails** — `pytest tests/test_persona_pregate.py::test_persona_photo_pregate_blocks_before_paid_call -v` → FAIL (гейта нет, платный зван).
- [ ] **Step 3: Implement** — в начале `handle_persona_photo` (после дешёвых проверок, до `generate_photo`):

```python
    _est = float(os.getenv("PERSONA_PHOTO_USD", "0.05"))
    _ok, _reason = check_limit(chat_id, estimated_usd=_est)
    if not _ok:
        send(chat_id, f"🚫 {_reason}")
        return
```

- [ ] **Step 4: Run — verify passes** → PASS. + позитивный тест (allow → платный зван, факт записан).
- [ ] **Step 5: Мутация обе стороны** — убрать `return` после отказа → платный зван → тест RED; вернуть → GREEN.
- [ ] **Step 6: Commit** — `git commit -am "feat(money): pre-gate /persona_photo against audit limit"`

### Task 2: Пре-гейт `/persona_video` + `/persona_video_redo`

**Files:** Modify `tools/jarvis_smart_telegram_control.py` (`_persona_video_dispatch` 423); Test `tests/test_persona_pregate.py`

- [ ] **Step 1: Failing test** — мок `check_limit` в ctl; при отказе `PersonaVideoHandler.handle_video/handle_redo` НЕ зван, `record_cost` (ctl:508) НЕ зван, «🚫». Est = `PERSONA_VIDEO_USD` (0.40).
- [ ] **Step 2: Run — verify fails** → FAIL.
- [ ] **Step 3: Implement** — в `_persona_video_dispatch` (423) перед вызовом хендлера: `check_limit(chat_id, estimated_usd=float(os.getenv("PERSONA_VIDEO_USD","0.40")))`; отказ → `send("🚫 …")`; return. Один гейт покрывает и video, и redo (общий диспатч).
- [ ] **Step 4: Run — verify passes** → PASS.
- [ ] **Step 5: Мутация** — гейт снят → хендлер зван → RED; вернуть → GREEN.
- [ ] **Step 6: Commit** — `git commit -am "feat(money): pre-gate /persona_video[_redo] via dispatch"`

### Task 3: Пре-гейт `/persona_redo`

**Files:** Modify `persona_handler.py:642`; Test `tests/test_persona_pregate.py`

- [ ] **Step 1: Failing test** — est `PERSONA_REDO_USD` (0.05); отказ → BatchGenerator/redo не зван, `_record_user_cost`(690) не зван, «🚫».
- [ ] **Step 2: Run — verify fails** → FAIL.
- [ ] **Step 3: Implement** — `check_limit` в начале `handle_persona_redo`, отказ → return.
- [ ] **Step 4: Run — verify passes** → PASS.
- [ ] **Step 5: Мутация** → RED/GREEN.
- [ ] **Step 6: Commit** — `git commit -am "feat(money): pre-gate /persona_redo"`

### Task 4: Пре-гейт `/persona_batch` (est = per×N)

**Files:** Modify `persona_handler.py:1072`; Test `tests/test_persona_pregate.py`

- [ ] **Step 1: Failing test** — N фото; est = `PERSONA_BATCH_USD`(0.05)×N; проверить, что оценка масштабируется N (мок check_limit ловит estimated_usd==0.05×N); отказ → `generate_batch`(1110) не зван, `_record_user_cost`(1124) не зван.
- [ ] **Step 2: Run — verify fails** → FAIL.
- [ ] **Step 3: Implement** — вычислить N (как хендлер уже парсит кол-во), `_est = float(os.getenv("PERSONA_BATCH_USD","0.05"))*N`, `check_limit(chat_id, estimated_usd=_est)`; отказ → return.
- [ ] **Step 4: Run — verify passes** → PASS.
- [ ] **Step 5: Мутация** (в т.ч. est фикс на 1×, не N → тест на масштаб RED).
- [ ] **Step 6: Commit** — `git commit -am "feat(money): pre-gate /persona_batch (est per-photo × N)"`

### Task 5: `/me_done` — пре-гейт + факт-запись (сейчас нет ни того, ни другого)

**Files:** Modify `persona_handler.py:826` `_do_train_me_lora`; Test `tests/test_persona_pregate.py`

- [ ] **Step 1: Failing test** — est `TRAIN_ME_LORA_USD` (5.00); отказ → `train_flux_lora`(847) не зван. Успех → `_record_user_cost(chat_id, actual)` вызван (сейчас НЕ вызывается вообще — money-зуб: трата видна в /my_stats).
- [ ] **Step 2: Run — verify fails** → FAIL (нет гейта И нет записи).
- [ ] **Step 3: Implement** — (1) `check_limit(chat_id, estimated_usd=float(os.getenv("TRAIN_ME_LORA_USD","5.00")))` перед `train_flux_lora`; отказ → return. (2) провязать факт-запись стоимости на успешном завершении по образцу `_do_train_lora`→`on_success_cost` (`persona_handler.py:387`) — `_record_user_cost(chat_id, actual_cost)`. **⚠️ Если тренировка асинхронна (daemon)** — запись идёт через колбэк на завершении (см. Task 7 резерв).
- [ ] **Step 4: Run — verify passes** → PASS.
- [ ] **Step 5: Мутация** — убрать гейт → RED; убрать запись → money-зуб RED.
- [ ] **Step 6: Commit** — `git commit -am "feat(money): gate /me_done Me-LoRA + record cost (was in neither ledger)"`

### Task 6: Выпилить block_m-бюджет (enforcement) — Допущение №3

**Files:** Modify `lora_trainer.py:102-106`, `photo_generator.py:57-59`, `persona_creator.py` (log_expense→check_limit); Test `tests/test_block_m_budget_removed.py`

- [ ] **Step 1: Failing test** — заполнить мок-`CostTracker`/`expenses.jsonl` так, чтобы старый глобальный лимит «сработал бы» (>$10), и проверить, что платный путь `LoRATrainer`/`PhotoGenerator` **всё равно проходит** (block_m `DailyLimitExceeded` НЕ поднимается). Плюс зуб: реальная защита — `access_control.check_limit` (пере-existing) — остаётся.

```python
def test_photo_generator_does_not_raise_block_m_budget(monkeypatch):
    # даже при "переполненном" block_m-леджере генерация не блокируется block_m-бюджетом
    ...
    # НЕ ожидаем DailyLimitExceeded
```

- [ ] **Step 2: Run — verify fails** → FAIL (сейчас поднимает/зовёт check_limit).
- [ ] **Step 3: Implement** — удалить строки enforcement: `lora_trainer.py:102-106` (`check_limit`+`raise DailyLimitExceeded`), `photo_generator.py:57-59`. В `persona_creator.py` — `log_expense` оставить, но если он внутренне зовёт `check_limit`→raise, заменить на прямой append без бюджет-проверки (или ветку, глушащую raise). `DailyLimitExceeded`/`check_limit` в `block_m_common/cost_tracker.py` **не удалять** (может использоваться в других местах — сверить grep'ом; если нигде — пометить к удалению отдельно).
- [ ] **Step 4: Run — verify passes** → PASS. Регресс block_m/persona.
- [ ] **Step 5: Мутация** — вернуть enforcement → тест RED.
- [ ] **Step 6: Commit** — `git commit -am "refactor(money): remove inert global block_m budget (redundant with per-user audit limit)"`

### Task 7: (c) Резерв на старте тренировки + дельта на успехе — Допущение №4

**Files:** Modify `lora_trainer.py` (start_training 64 / _run_training 160-184), `persona_creator.py` (generate_seed_photos 137), `persona_handler.py` (_do_train_lora 360/387, _do_train_me_lora 826); Test `tests/test_training_reservation.py`

- [ ] **Step 1: Failing test** — драйв старта тренировки (мок Replicate + мок `record_cost`): (1) **на старте** `record_cost(chat_id, est)` вызван ДО запуска потока; (2) на успехе записана **дельта** `(actual − est)` (итог = actual); (3) симуляция «рестарта» (поток не доходит до успеха) → в леджере остаётся ровно `est` (не 0, не двойное). Est из env (`TRAIN_LORA_USD`, `PERSONA_SEED_USD`, `TRAIN_ME_LORA_USD`).

```python
def test_reservation_recorded_at_kickoff_and_reconciled_on_success(monkeypatch):
    recs = []
    monkeypatch.setattr(..., "_record_user_cost", lambda cid, amt: recs.append(amt))
    # старт → recs == [est]; успех actual=1.8, est=2.0 → recs == [2.0, -0.2]; sum==1.8
    ...
def test_reservation_survives_restart(monkeypatch):
    # поток падает до on_success → recs == [est] (честный резерв)
```

- [ ] **Step 2: Run — verify fails** → FAIL (резерва нет; on_success пишет полный факт).
- [ ] **Step 3: Implement** —
  1. В `_do_train_lora`/`_do_train_me_lora`/seed-старте: перед запуском daemon-потока `_record_user_cost(chat_id, est)` (резерв). Гейт `check_limit` из Task 1-5 остаётся ДО резерва.
  2. Изменить `on_success_cost` семантику: писать `actual - est` (дельта), а не полный actual. Провязать `est` в колбэк (замыкание). Так итог = est + (actual−est) = actual; при неуспехе остаётся est.
  3. `record_cost` должен принимать отрицательную сумму (дельта-refund при actual<est) — сверить `cost_tracker.py:93` (обычный сумматор — минус уменьшит today). Если не поддерживает минус — clamp дельты ≥ −(est) и задокументировать.
- [ ] **Step 4: Run — verify passes** → PASS.
- [ ] **Step 5: Мутация** — убрать резерв → restart-тест RED; оставить on_success полным (не дельта) → double-count тест RED.
- [ ] **Step 6: Commit** — `git commit -am "feat(money): reserve estimated training cost at kickoff, reconcile delta on success"`

### Task 8: Полный регресс

- [ ] Baseline (до правок) diff имён падений vs after (как в Арке 1/2). Флаки — изоляцией. «Зелёно» = не добавили новых падений vs baseline (~130 known, order-flak bolt/figma).
- [ ] Все новые money-зубы green; персона/trainer/creator-регресс чист.
- [ ] Commit при необходимости фиксов регресса.

---

## Риски

1. **Двойное списание (a).** Пре-гейт НЕ должен добавлять `record_cost` — факт уже пишется на успехе. Зуб: тест проверяет, что при allow ровно ОДНА факт-запись.
2. **Line-drift.** Номера строк — из разведки @da2312e; при исполнении сверять хендлеры по имени, не по номеру.
3. **Асинхронность `/me_done`.** Если Me-LoRA тренировка идёт в daemon-потоке (как `_do_train_lora`), факт-запись Task 5 идёт через колбэк на завершении, а не синхронно — иначе рестарт снова слепой. Task 7 резерв это страхует.
4. **Отрицательная дельта в audit (c).** Если `record_cost` не поддерживает минус — дельта-refund при actual<est невозможен; тогда резерв=верхняя граница, факт может завысить. Задокументировать; либо clamp.
5. **block_m log_expense после выпила бюджета.** Оставляем записи (безвредно), но они пишут в мёртвый леджер — не путать с audit. Полное удаление block_m-леджера = отдельный техдолг.
6. **Пре-существующие 130 падений** (runpod/pydantic/mobile_ux) — baseline, не money. «Зелёно» = не хуже baseline.

## Что НЕ трогаем

- Живой факт-record на успехе в существующих хендлерах (только добавляем пре-гейт перед ним).
- Мёртвый `handle_persona_video` `persona_handler.py:570` (не диспатчится).
- Уже гейтнутые Аркой 1 команды (`create_persona`, `/train_lora`, `/me_swap_photo/video`, Photo Studio, faceswap/enhance).
- Полное удаление block_m-леджера/`analytics.py`/`DailyLimitExceeded`-класса (отдельный техдолг — Task 6 убирает только enforcement-вызовы).
- **Техдолг (c-полное):** startup-скан `lora_jobs.json` orphan-джобов (статус running после рестарта) + запрос фактической стоимости у Replicate + коррекция леджера — отдельная арка.

---

## Self-review

- **Покрытие:** (a) 5 команд+me_done = T1-T5; (b) выпил = T6; (c) резерв+дельта = T7; регресс T8. ✔
- **Placeholder-скан:** код в шагах приведён; env-дефолты явны; line-номера помечены как «сверить». ✔
- **Типы/имена:** `check_limit(uid, estimated_usd=)`, `_record_user_cost(cid, amt)`, `record_cost(uid, username, amt)` — согласованы с разведкой. ✔
- **Открытый вопрос для Daniil:** Допущение №2 ($5 vs $2 для /me_done) и №4 (отрицательная дельта) — подтвердить при ОК.
