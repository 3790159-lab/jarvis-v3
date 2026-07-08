# IR Merge + Reorder (Ф0 + Ф1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Оживить интент-роутер (IR-1) в проде и поднять его в `classify_message` ВЫШЕ эвристических legacy-веток, чтобы командообразные фразы («глянь что с ботом», «проверь сайт X») распознавались роутером, а не проваливались в ungated-ассистента.

**Architecture:** Ф0 — rebase ветки `intent-router` (7 IR-коммитов от `33123d3`) на текущий прод `84f1823` (области не пересекаются → без конфликтов), прогон тестов, фиксация свежего regress-baseline. Ф1 — в том же worktree: перенести единственный вызов `intent_router.resolve()` из хвоста `classify_message` в позицию сразу после детерминированных/FSM-перехватов (перед первой эвристикой `health`), сменив семантику промаха с «терминировать» на «провалиться дальше» (route/clarify → return; uncertain/none → fall-through). Терминальный catch-all возвращается к прежнему `chat`→ассистенту (его command-aware форма — отдельная Ф3). Мердж — ОДИН, после Ф1, чтобы прод не увидел промежуточный «плохой порядок».

**Tech Stack:** Python 3.14, stdlib `difflib` (в IR-модуле), pytest + мутационные «зубы», git worktree/rebase. Бот перезапускается через `JarvisBotGuardian` (PYTHONUTF8=1).

---

## Контекст и факты разведки (проверено чтением кода)

- **Живой прод:** ветка `phase-4.0-unified-jarvis` @ `84f1823`. Бот исполняет `C:\jarvis\tools\jarvis_smart_telegram_control.py` — IR там НЕТ (grep `intent_router` = 0, `tools/intent_router.py` отсутствует).
- **IR-код:** worktree `C:/jarvis_worktrees/intent-router`, ветка `intent-router` @ `8936463` = `33123d3` + 7 коммитов (`a362f22`→`8936463`). 39 IR-тестов зелёные (18 `tests/test_intent_router.py` + 21 `tests/test_intent_router_wiring.py`). Модуль `tools/intent_router.py` (~11.6 КБ).
- **Merge-base** `intent-router` ↔ `phase-4.0` = `33123d3` (предок обоих). Prod ушёл на +2: `17fb691` (devtask start-message, трогает control-файл только @1376 — `_devtask_confirm`) и `84f1823` (observe-модуль). **Ни один не пересекается** с IR-областью (`classify_message` @3308–3571, `run_intent` @3942+, новый файл `intent_router.py`, IR-тесты) → rebase без конфликтов.
- **Текущая позиция IR в worktree (`classify_message`):** хвост, строки `3557–3571`, ПОСЛЕ ~30 legacy-веток. Заменяет только бывший catch-all `chat`. Именно из-за хвостовой вставки `_strong_image_patterns`@3491 (→`generate`), `health`@3399 (узкий keyword), и прочие эвристики перехватывают фразу ДО IR (зафиксировано в RESUME).
- **Обработчики IR в `run_intent` (worktree, уже есть, НЕ трогаем):**
  - `ir_route`@3954: `if not arg and _ir.auto_exec_ok(cmd): handle(chat_id, cmd)` (free+read-only → сразу) `else: _ir_send_confirm(...)` (платно/параметр → кнопка `ir:run` с ценой). **Paid-confirm уже реализован.**
  - `ir_clarify`@3962 → `_ir_send_clarify` (кнопки выбора).
  - `ir_uncertain`@3965 / `ir_unknown`@3969 → `_ir_unknown` («🤷 Не понял… /menu»).
- **Публичный контракт `intent_router`:** `resolve(text, role, friend_allowed=None) -> IRResult{decision in "route"|"clarify"|"uncertain"|"none", candidates:[.cmd,.arg]}`; `auto_exec_ok(cmd)`, `is_paid(cmd)`, `price_hint(cmd)`, `build_corpus()`.

### Решения по ходу (нужен ОК Артёма/Daniil ДО кода)

1. **Сцепка мерджа:** мердж делаем ОДИН — после Ф1 (rebase+reorder оба в worktree). Прод не увидит промежуточную «хвостовую» версию → один рестарт, без окна странного поведения. (Альтернатива из прежнего наброска — мерджить на Ф0 — даёт двойной рестарт и временно живой плохой порядок.) **Рекомендую сцепку.**
2. **Область Ф1 = только порядок.** Терминальный catch-all после переноса IR = прежний `chat`→ассистент (как в проде до IR). Его command-aware «середина»-форма (preamble + «/menu» на нераспознанное) — **Ф3, отдельная спека.** Здесь ungated-ассистент временно остаётся терминалом (Ф1 чинит health/browse, НЕ трогает money-дыру generate — та в Ф2).
3. **Точка вставки IR** = сразу после блока `file_triggers` (worktree ~строка 3397), ПЕРЕД `health`@3399. Всё выше (detect_command, pending/FSM, greeting, small_talk, continuation, capabilities, can_you, file_triggers) остаётся ПЕРЕД IR (детерминированные/identity-safe перехваты не должны хайджекаться роутером).

---

## File Structure

- **Modify:** `tools/jarvis_smart_telegram_control.py` (в worktree `intent-router`)
  - `classify_message` (~3308–3571): удалить хвостовой IR-блок (3557–3571); восстановить терминал `return {"intent": "chat", "query": raw}`; вставить ранний IR-гейт после `file_triggers`.
- **Modify (tests):** `tests/test_intent_router_wiring.py` — добавить тесты порядка/fall-through/сохранности legacy.
- **No new files.** `tools/intent_router.py` и его контракт не меняются. `run_intent` IR-обработчики не меняются.

---

## Ф0 — Rebase IR-1 на актуальный прод

### Task 0.1: Rebase ветки intent-router на 84f1823

**Files:**
- Worktree: `C:/jarvis_worktrees/intent-router` (ветка `intent-router`)

- [ ] **Step 1: Убедиться, что worktree чист**

Run: `cd /c/jarvis_worktrees/intent-router && git status --porcelain`
Expected: пусто (нет незакоммиченных изменений).

- [ ] **Step 2: Rebase 7 IR-коммитов на прод**

Run:
```bash
cd /c/jarvis_worktrees/intent-router
git fetch . phase-4.0-unified-jarvis 2>/dev/null; git rebase 84f1823
```
Expected: `Successfully rebased and updated refs/heads/intent-router.` Без конфликтов (области disjoint). Если конфликт — СТОП, разобрать вручную (не ожидается).

- [ ] **Step 3: Проверить, что IR-код на месте после rebase**

Run: `grep -c "intent_router" tools/jarvis_smart_telegram_control.py && ls -la tools/intent_router.py`
Expected: ≥14 совпадений, файл существует.

- [ ] **Step 4: Прогнать 39 IR-тестов**

Run: `cd /c/jarvis_worktrees/intent-router && python -m pytest tests/test_intent_router.py tests/test_intent_router_wiring.py -q -p no:cacheprovider`
Expected: `39 passed` (18 + 21). Если падают из-за rebase — СТОП.

- [ ] **Step 5: Зафиксировать свежий regress-baseline на rebased-вершине**

Run:
```bash
cd /c/jarvis_worktrees/intent-router
python -m pytest tests/ -q -p no:cacheprovider --continue-on-collection-errors --tb=no 2>&1 | tail -3
```
Expected: сводка вида `NNN failed, MMMM passed` (ожидаемо ~129–132 failed — flak-band bolt/figma/landing, см. RESUME). Записать число в `scratchpad/ir_baseline_failed.txt` для сравнения в Ф1.

> **Примечание:** мердж в прод здесь НЕ делаем (см. Решение 1). Ф0 оставляет rebased-worktree готовым для Ф1. Никаких коммитов на этом шаге.

---

## Ф1 — Reorder: IR первым из свободных путей

### Task 1.1: Тест — IR перехватывает command-фразу ДО эвристик (health)

**Files:**
- Test: `tests/test_intent_router_wiring.py`

- [ ] **Step 1: Написать падающий тест порядка**

```python
def test_classify_ir_routes_health_before_catchall(monkeypatch):
    """«глянь что с ботом» должно уйти в ir_route /health, НЕ в chat/ассистента."""
    import tools.jarvis_smart_telegram_control as bot
    result = bot.classify_message("глянь что с ботом", {})
    assert result["intent"] == "ir_route"
    assert result["command"] == "/health"
```

- [ ] **Step 2: Прогнать — убедиться, что падает**

Run: `python -m pytest tests/test_intent_router_wiring.py::test_classify_ir_routes_health_before_catchall -v`
Expected: FAIL — сейчас `classify_message` возвращает `{"intent": "chat", ...}` (IR в хвосте, а до хвоста фраза не долетает как ir_route; фактически при текущей хвостовой вставке она долетает, НО задача теста — зафиксировать раннюю позицию; если PASS на хвостовой версии — усилить тест фразой, которую перехватывает legacy-ветка, напр. Task 1.2).

> **Замечание по TDD:** «глянь что с ботом» проходит все legacy-ветки и на ХВОСТОВОЙ версии уже даёт ir_route. Настоящий «зуб порядка» — Task 1.2 (фраза, которую legacy перехватывает раньше IR). Task 1.1 фиксирует, что health-фраза остаётся ir_route ПОСЛЕ переноса (регресс-страховка).

### Task 1.2: Тест-зуб — IR перехватывает раньше legacy `health`-keyword

**Files:**
- Test: `tests/test_intent_router_wiring.py`

- [ ] **Step 1: Написать падающий тест-зуб**

```python
def test_classify_ir_precedes_legacy_health_keyword(monkeypatch):
    """Фраза со словом 'статус' сейчас перехватывается legacy health@3399 как
    {'intent':'health'} БЕЗ команды. После переноса IR встаёт РАНЬШЕ и даёт
    ir_route с конкретной командой из реестра."""
    import tools.jarvis_smart_telegram_control as bot
    result = bot.classify_message("покажи статус git", {})
    # IR должен смэтчить /git_status (реестр), не отдать голый legacy health
    assert result["intent"] == "ir_route"
    assert result["command"] == "/git_status"
```

- [ ] **Step 2: Прогнать — убедиться, что падает**

Run: `python -m pytest tests/test_intent_router_wiring.py::test_classify_ir_precedes_legacy_health_keyword -v`
Expected: FAIL — legacy `health`@3399 ловит «статус» → `{"intent": "health"}` ДО хвостового IR.

### Task 1.3: Тест — fall-through сохраняет legacy на промахе IR

**Files:**
- Test: `tests/test_intent_router_wiring.py`

- [ ] **Step 1: Написать падающий тест fall-through**

```python
def test_classify_ir_miss_falls_through_to_research(monkeypatch):
    """Не-командная фраза (research) НЕ должна хайджекаться IR — при uncertain/none
    классификация проваливается в существующую legacy-ветку research."""
    import tools.jarvis_smart_telegram_control as bot
    result = bot.classify_message("найди отзывы о телефоне пионер", {})
    assert result["intent"] == "research"
```

- [ ] **Step 2: Прогнать — убедиться, что падает**

Run: `python -m pytest tests/test_intent_router_wiring.py::test_classify_ir_miss_falls_through_to_research -v`
Expected: FAIL (после Task 1.4 станет PASS; на хвостовой версии сейчас может уже PASS — тогда это регресс-страховка fall-through).

### Task 1.4: Реализация — перенести IR-гейт вверх + восстановить терминал chat

**Files:**
- Modify: `tools/jarvis_smart_telegram_control.py`
  - Удалить хвостовой IR-блок (worktree ~3552–3571).
  - Восстановить терминал: `return {"intent": "chat", "query": raw}`.
  - Вставить ранний IR-гейт сразу после блока `file_triggers` (после строки с `return {"intent": file_intent, ...}` / перед `if any(x in t for x in ["статус", "здоровье системы", ...])`@3399).

- [ ] **Step 1: Удалить хвостовой IR-блок и восстановить терминал `chat`**

Заменить блок (worktree ~3552–3571, начиная с комментария «Ступень стоит ПОСЛЕ…» и до `return {"intent": "ir_unknown", "query": raw}` в `except`) на:

```python
    return {"intent": "chat", "query": raw}
```

- [ ] **Step 2: Вставить ранний IR-гейт после `file_triggers`**

Сразу ПОСЛЕ блока `file_triggers` (после `for file_intent, ftriggers ... return {"intent": file_intent, "query": raw}`, ~строка 3397) и ПЕРЕД `if any(x in t for x in ["статус", ...]): return {"intent": "health"}`@3399 вставить:

```python
    # ── IR-1 (intent-router): ПЕРВЫМ из свободных путей ─────────────────────
    # После детерминированных/FSM/identity-safe перехватов (slash, pending,
    # greeting, small_talk, capabilities, can_you, file_triggers), но ДО всех
    # эвристических keyword-веток (health/generate/research/…). route/clarify —
    # short-circuit; uncertain/none — ПРОВАЛИТЬСЯ дальше в legacy (не терминировать),
    # чтобы не сломать существующие content-флоу. Роль admin (свободный текст
    # friend идёт узким путём в handle()). Роутер не должен ронять классификацию.
    try:
        from tools import intent_router as _ir
        _res = _ir.resolve(raw, "admin")
        if _res.decision == "route":
            _c = _res.candidates[0]
            return {"intent": "ir_route", "command": _c.cmd, "arg": _c.arg, "query": raw}
        if _res.decision == "clarify":
            return {"intent": "ir_clarify",
                    "candidates": [c.cmd for c in _res.candidates], "query": raw}
        # uncertain / none → fall-through к legacy-эвристикам ниже
    except Exception:
        pass  # безопасный откат: продолжаем legacy-классификацию
```

- [ ] **Step 3: Прогнать три reorder-теста — убедиться, что зелёные**

Run: `python -m pytest tests/test_intent_router_wiring.py::test_classify_ir_routes_health_before_catchall tests/test_intent_router_wiring.py::test_classify_ir_precedes_legacy_health_keyword tests/test_intent_router_wiring.py::test_classify_ir_miss_falls_through_to_research -v`
Expected: 3 passed.

- [ ] **Step 4: Прогнать все IR-тесты (регресс IR-контракта)**

Run: `python -m pytest tests/test_intent_router.py tests/test_intent_router_wiring.py -q -p no:cacheprovider`
Expected: все passed (39 старых + 3 новых = 42). Если старый wiring-тест завязан на хвостовую позицию/`ir_unknown`-терминал — обновить его под новую семантику (fall-through), НЕ ослабляя.

- [ ] **Step 5: Commit**

```bash
git add tools/jarvis_smart_telegram_control.py tests/test_intent_router_wiring.py
git commit -m "feat(ir): IR-1 reorder — router first among free paths, legacy as fall-through"
```

### Task 1.5: Зуб-мутация позиции (доказать, что порядок реально держит)

**Files:**
- Test: `tests/test_intent_router_wiring.py` (используем Task 1.2)

- [ ] **Step 1: Мутация — временно вернуть IR-гейт в хвост**

Вручную (в рабочем дереве, БЕЗ коммита) перенести вставленный IR-блок обратно ниже `_strong_image_patterns`/`health`.

- [ ] **Step 2: Прогнать зуб — должен покраснеть**

Run: `python -m pytest tests/test_intent_router_wiring.py::test_classify_ir_precedes_legacy_health_keyword -v`
Expected: FAIL (legacy `health` снова перехватывает «статус»). Это доказывает, что тест ловит регресс порядка.

- [ ] **Step 3: Откатить мутацию**

Run: `git checkout tools/jarvis_smart_telegram_control.py` — вернуть корректную (Task 1.4) версию. Прогнать Step 2 повторно → PASS.

### Task 1.6: Полный регресс-гейт vs baseline

**Files:**
- Worktree `intent-router`

- [ ] **Step 1: Прогнать канон-регресс**

Run:
```bash
cd /c/jarvis_worktrees/intent-router
python -m pytest tests/ -q -p no:cacheprovider --continue-on-collection-errors --tb=no 2>&1 | tail -3
```
Expected: `failed` ≤ baseline из `scratchpad/ir_baseline_failed.txt` (± flak-band bolt/figma/landing). **NEW-падения = 0.** Если NEW>0 — точный ID-diff (`comm` через temp-файлы), каждый NEW прогнать solo; при реальном регрессе — СТОП.

- [ ] **Step 2: Зафиксировать вердикт регресса в отчёт**

Записать в отчёт: baseline N failed → after M failed, NEW=0 (или список), touched-области (intent_router / wiring / classify) зелёные.

---

## Живой прогон (после ОК на мердж — НЕ часть кода)

> После ОК Артёма/Daniil: мердж (rebase уже сделан → чистый FF `84f1823..intent-router`) → `phase-4.0-unified-jarvis` → рестарт через `JarvisBotGuardian` (PYTHONUTF8=1) → отчёт о boot (0 ERROR/Traceback, heartbeat, native ☰) → живой тап:
> - «глянь что с ботом» → должен уйти в /health (ir_route auto-exec), НЕ ассистент.
> - «проверь сайт example.com» → ir_route/ir_clarify на /browse_check (платно → confirm-кнопка), НЕ «нет инструмента».
> - «сделай фото картошки» → **ожидаемо ещё БЕЗ ворот** (legacy `generate`@3491 ловит; чинится в Ф2). Зафиксировать поведение как вход в Ф2.
> - Sanity: «привет» → greeting; «найди отзывы о X» → research (fall-through цел).

---

## Self-Review

**1. Spec coverage:**
- Ф0 (rebase IR на прод) → Task 0.1 ✅
- Ф1 директива «IR первым из свободных путей после FSM, до ассистента» → Task 1.4 (ранняя вставка после file_triggers) ✅
- «ассистент — только нераспознанное» → терминал `chat` сохранён как fall-through (command-aware форма = Ф3, вне области) ✅
- health/browse фикс → Task 1.1/1.2 + живой прогон ✅
- «не сломать legacy-флоу» → Task 1.3 fall-through + Task 1.6 регресс-гейт ✅
- Зуб порядка → Task 1.5 мутация ✅

**2. Placeholder scan:** нет TBD/«handle edge cases»/«similar to Task N» — весь код приведён дословно. ✅

**3. Type consistency:** `resolve().decision` ∈ {route,clarify,uncertain,none}; `.candidates[i].cmd/.arg`; `ir_route`/`ir_clarify` dict-ключи (`command`,`arg`,`candidates`) совпадают с обработчиками `run_intent`@3954-3963 (не тронуты). ✅

**Известный пробел (осознанный, вне Ф0/Ф1):** «сделай фото» money-дыра (`generate`@3491 exec без ворот) — Ф2. Command-aware ассистент — Ф3. IR-2 (Haiku) — Ф4.

---

## Рамки

- Тесты только на моках (реальный API/сеть = СТОП). Кода не писать до ОК.
- Неоднозначность доступа/денег = СТОП + вопрос в Telegram (chat_id 237616472).
- НЕ мерджить, НЕ рестартить бота до ОК. Мердж — ОДИН, после Ф1.
- Откат тривиален: прод `84f1823` не тронут до мерджа; worktree можно снести.
