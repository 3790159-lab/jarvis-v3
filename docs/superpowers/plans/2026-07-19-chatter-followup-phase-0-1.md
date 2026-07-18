# Chatter Follow-up — Phase 0 + Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Заложить фундамент курьерского follow-up: детект обещания «вернусь», персистентная модель `follow_ups`, детерминированный триггер эскалации, и регистрация pending-follow-up при доставленной карточке — БЕЗ единой отправки лиду (I1).

**Architecture:** Чистая ф-я `promises_return` (core/follow_up.py) + таблица `follow_ups` в SQLite (Store) + не-suppress триггер `return_promise` в `deterministic_escalation` + регистрация в `run._escalation_pass` (только при delivered, I3). Проактивной отправки НЕТ до Фазы 3 (I1).

**Tech Stack:** Python 3.14, sqlite3, pytest. Спек: `docs/superpowers/specs/2026-07-19-chatter-followup-courier-design.md`.

**Рабочее место:** изолированный worktree `C:/jarvis-followup` (ветка `chatter-followup`). Главное дерево `C:\jarvis` = прод на `phase-4.0`, его НЕ трогаем. **Никогда не запускать `chatter.telethon_run` из worktree** (путь содержит подстроку `C:\jarvis` → матчится CIM-фильтром гардиана). Только pytest. Живые дрилы — через деплой в главное дерево (Фаза 3+).

**Команды (из worktree):** `cd /c/jarvis-followup && /c/jarvis/.venv/Scripts/python.exe -m pytest <путь> -q`

---

## File Structure

- **Create** `chatter/core/follow_up.py` — чистые ф-и follow-up (Фаза 1: `promises_return`; позже: `lead_closed_topic`, `follow_up_send_decision`). Зеркалит стиль `obligations.py` (ноль сети/LLM).
- **Create** `tests/chatter/test_follow_up.py` — юниты `promises_return` (+негейт).
- **Modify** `chatter/storage/db.py` — таблица `follow_ups` (полная схема) + Store-методы CRUD. Рядом с `get_runtime_flag_ts`.
- **Modify** `tests/chatter/test_store.py` — юниты Store-методов follow-up.
- **Modify** `chatter/core/escalation.py` — триггер `return_promise` (не-suppress) в `deterministic_escalation`.
- **Modify** `tests/chatter/test_escalation.py` — юнит триггера.
- **Modify** `chatter/core/console.py` — строка карточки `card_followup_hint` (RU/EN/UK).
- **Modify** `chatter/run.py` — регистрация follow-up в `_escalation_pass` + follow-up-строка в карточке. Импорт `dataclasses`, `promises_return`.
- **Modify** `tests/chatter/test_escalation_wiring.py` — тесты регистрации + **I1 (лиду ничего не ушло)**.

---

## Phase 0 — Case A (промпт-упрочнение)

### Task 0.1: Директива «отвечай из ЗНАНИЯ сразу» в _STYLE

**Files:**
- Modify: `chatter/core/brain.py` (`_STYLE`, строки ~7-19)
- Test: `tests/chatter/test_brain.py`

- [ ] **Step 1: Write the failing test**

```python
def test_style_instructs_answer_from_knowledge_immediately():
    from chatter.config.loader import load_config
    from chatter.core.brain import build_system_prompt
    from pathlib import Path
    CLIENTS = Path(__file__).resolve().parents[2] / "chatter" / "clients"
    prompt = build_system_prompt(load_config(CLIENTS, "demo"))
    low = prompt.casefold()
    # Case A: если ответ есть в ЗНАНИЯ — отвечать в моменте, не «уточню и вернусь».
    assert "ответь сразу" in low
    assert "уточню и вернусь" in low  # правило про то, КОГДА уместно откладывать
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/jarvis-followup && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/chatter/test_brain.py::test_style_instructs_answer_from_knowledge_immediately -q`
Expected: FAIL (`assert "ответь сразу" in low` — фразы нет в _STYLE).

- [ ] **Step 3: Add the directive to _STYLE**

В `chatter/core/brain.py`, внутри строки `_STYLE`, ПОСЛЕ предложения про «не выдумывай цены…» добавь:

```python
    "Если ответ на вопрос ЕСТЬ в разделе ЗНАНИЯ — ответь сразу и по существу, "
    "приведи факт из ЗНАНИЯ прямо сейчас. Не отвечай «уточню и вернусь», когда "
    "ответ уже есть в ЗНАНИЯ; откладывай («уточню и вернусь») только когда в "
    "ЗНАНИЯ этого правда нет. "
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/jarvis-followup && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/chatter/test_brain.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /c/jarvis-followup && git add chatter/core/brain.py tests/chatter/test_brain.py && git commit -m "chatter(followup Ф0/Case A): _STYLE — отвечай из ЗНАНИЯ сразу, не откладывай

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

> **Примечание (честно):** это промпт-директива, не детерминированный энфорсмент. Настоящая проверка Case A — живой дрил in-knowledge вопросов (цена/часы/48ч/оплата) при деплое в главное дерево. Юнит выше только стережёт наличие директивы.

---

## Phase 1 — модель + детект + регистрация (МОЛЧИТ лиду, I1)

### Task 1.1: `promises_return` — детект обещания вернуться (+негейт)

**Files:**
- Create: `chatter/core/follow_up.py`
- Test: `tests/chatter/test_follow_up.py`

- [ ] **Step 1: Write the failing test**

```python
from chatter.core.follow_up import promises_return


def test_detects_core_return_promises_ru():
    assert promises_return("Хороший вопрос — уточню детали и вернусь.")
    assert promises_return("Отвечу позже, как узнаю.")
    assert promises_return("Дам знать, как только выясню.")
    assert promises_return("Напишу вам, когда будет ответ.")


def test_detects_return_promises_uk_en():
    assert promises_return("Добре, повернуся з відповіддю.")
    assert promises_return("Відповім пізніше.")
    assert promises_return("Sure, I'll get back to you.")
    assert promises_return("I'll let you know.")


def test_negate_ball_in_lead_court_is_not_a_promise():
    # Мяч у лида — НЕ обещание возврата.
    assert not promises_return("Вернёмся к этому, когда определитесь.")
    assert not promises_return("Как решите — дайте знать когда удобно.")


def test_plain_reply_is_not_a_return_promise():
    assert not promises_return("Съёмка стоит 15000 грн.")
    assert not promises_return("Наверное, вам подойдёт портретная съёмка.")  # «наверное»≠«вернусь»
    assert not promises_return("")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/jarvis-followup && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/chatter/test_follow_up.py -q`
Expected: FAIL (`ModuleNotFoundError: chatter.core.follow_up`).

- [ ] **Step 3: Create `chatter/core/follow_up.py`**

```python
"""Follow-up (курьер): чистые ф-и. Ноль сети/LLM/Telethon — зеркалит
obligations.py. Фаза 1: детект обещания «вернусь». Позже сюда добавятся
lead_closed_topic и follow_up_send_decision.
"""
from __future__ import annotations

# Обещание Ани вернуться с ответом. Специфичные формы (не стемы), чтобы не ловить
# «наверное» (в нём «верн» как подстрока) и «верните» (слово лида про возврат).
_RETURN_PHRASES = (
    # ru
    "вернусь", "вернёмся", "вернемся",
    "отвечу поз", "дам знать", "напишу вам", "напишу поз",
    # uk
    "повернусь", "повернуся", "повернемося",
    "відповім піз", "дам знати",
    # en
    "get back to you", "let you know", "follow up",
)

# Мяч у лида — это НЕ обещание возврата Ани (условие на стороне лида).
_NEGATE_PHRASES = (
    "когда определит", "как решит", "когда вы ", "когда будете готов",
    "дайте знать когда", "как только вы",
    "коли визначит", "let me know when",
)


def promises_return(reply: str) -> bool:
    """True, если ответ Ани обещает вернуться с ответом («уточню и вернусь»,
    «отвечу позже», «дам знать»…). Негейт: «вернёмся когда определитесь» —
    условие на стороне лида, не обещание Ани."""
    low = (reply or "").casefold()
    if any(n in low for n in _NEGATE_PHRASES):
        return False
    return any(p in low for p in _RETURN_PHRASES)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/jarvis-followup && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/chatter/test_follow_up.py -q`
Expected: PASS (все 4).

- [ ] **Step 5: Commit**

```bash
cd /c/jarvis-followup && git add chatter/core/follow_up.py tests/chatter/test_follow_up.py && git commit -m "chatter(followup Ф1): promises_return — детект обещания вернуться (+негейт)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 1.2: таблица `follow_ups` + Store-методы

**Files:**
- Modify: `chatter/storage/db.py` (схема в `__init__`; методы рядом с `get_runtime_flag_ts`)
- Test: `tests/chatter/test_store.py`

- [ ] **Step 1: Write the failing test**

```python
def test_follow_up_register_and_get(tmp_path):
    s = Store(tmp_path / "c.db")
    assert s.get_follow_up("42:demo") is None
    s.register_follow_up("42:demo", topic="скидку?", lead_last_ts=100.0,
                         card_ref="bot:1:7", now=100.0)
    fu = s.get_follow_up("42:demo")
    assert fu["state"] == "pending_owner"
    assert fu["mode"] == "verbatim"          # дефолт
    assert fu["topic"] == "скидку?"
    assert fu["card_ref"] == "bot:1:7"
    assert fu["lead_last_ts"] == 100.0
    assert fu["holding_sent"] == 0

def test_follow_up_reregister_supersedes(tmp_path):
    # Новое обещание по тому же контакту сбрасывает старое (одно на контакт).
    s = Store(tmp_path / "c.db")
    s.register_follow_up("42:demo", topic="A", lead_last_ts=1.0, card_ref="bot:1:7", now=1.0)
    s.set_follow_up("42:demo", state="ready", owner_answer="да", mode="instruction")
    s.register_follow_up("42:demo", topic="B", lead_last_ts=2.0, card_ref="bot:1:9", now=2.0)
    fu = s.get_follow_up("42:demo")
    assert fu["state"] == "pending_owner"    # сброшено
    assert fu["topic"] == "B"
    assert fu["owner_answer"] is None         # сброшено
    assert fu["mode"] == "verbatim"           # сброшено к дефолту

def test_follow_up_set_and_list_by_state(tmp_path):
    s = Store(tmp_path / "c.db")
    s.register_follow_up("42:demo", topic="A", lead_last_ts=1.0, card_ref="r", now=1.0)
    s.register_follow_up("99:demo", topic="B", lead_last_ts=1.0, card_ref="r2", now=1.0)
    s.set_follow_up("42:demo", state="ready", owner_answer="ответ", owner_answer_ts=5.0)
    ready = s.follow_ups_by_state("ready")
    assert [f["contact_id"] for f in ready] == ["42:demo"]
    assert ready[0]["owner_answer"] == "ответ" and ready[0]["owner_answer_ts"] == 5.0
    assert {f["contact_id"] for f in s.follow_ups_by_state("pending_owner")} == {"99:demo"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/jarvis-followup && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/chatter/test_store.py -k follow_up -q`
Expected: FAIL (`AttributeError: 'Store' object has no attribute 'register_follow_up'`).

- [ ] **Step 3: Add the table to the schema**

В `chatter/storage/db.py`, в блоке `CREATE TABLE IF NOT EXISTS` внутри `__init__` (рядом с `runtime_flags`/`console_cards`), добавь:

```python
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS follow_ups (
                contact_id TEXT PRIMARY KEY,
                topic TEXT,
                promised_ts REAL,
                lead_last_ts REAL,
                card_ref TEXT,
                state TEXT NOT NULL DEFAULT 'pending_owner',
                mode TEXT NOT NULL DEFAULT 'verbatim',
                owner_answer TEXT,
                owner_answer_ts REAL,
                holding_sent INTEGER NOT NULL DEFAULT 0,
                created_ts REAL,
                updated_ts REAL
            )
        """)
```

- [ ] **Step 4: Add Store methods**

В `chatter/storage/db.py`, сразу после `get_runtime_flag_ts`:

```python
    # --- follow-ups (курьер) ------------------------------------------------
    _FOLLOW_UP_SETTABLE = frozenset({
        "topic", "lead_last_ts", "card_ref", "state", "mode",
        "owner_answer", "owner_answer_ts", "holding_sent",
    })

    def register_follow_up(self, contact_id: str, *, topic: str, lead_last_ts: float,
                           card_ref: str, now: float) -> None:
        """Завести/освежить pending follow-up (одно на контакт). Новое обещание
        ПОЛНОСТЬЮ вытесняет прежнее (сброс state/mode/owner_answer/holding)."""
        with self._lock:
            self._conn.execute(
                """INSERT INTO follow_ups
                   (contact_id, topic, promised_ts, lead_last_ts, card_ref,
                    state, mode, owner_answer, owner_answer_ts, holding_sent,
                    created_ts, updated_ts)
                   VALUES (?,?,?,?,?, 'pending_owner','verbatim', NULL, NULL, 0, ?, ?)
                   ON CONFLICT(contact_id) DO UPDATE SET
                     topic=excluded.topic, promised_ts=excluded.promised_ts,
                     lead_last_ts=excluded.lead_last_ts, card_ref=excluded.card_ref,
                     state='pending_owner', mode='verbatim',
                     owner_answer=NULL, owner_answer_ts=NULL, holding_sent=0,
                     updated_ts=excluded.updated_ts""",
                (contact_id, topic, now, lead_last_ts, card_ref, now, now))
            self._conn.commit()

    def get_follow_up(self, contact_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM follow_ups WHERE contact_id=?", (contact_id,)).fetchone()
        return dict(row) if row else None

    def set_follow_up(self, contact_id: str, **fields) -> None:
        """Точечно обновить поля follow-up. Разрешён только whitelist полей."""
        bad = set(fields) - self._FOLLOW_UP_SETTABLE
        if bad:
            raise ValueError(f"нельзя менять поля follow-up: {sorted(bad)}")
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        vals = list(fields.values())
        with self._lock:
            self._conn.execute(
                f"UPDATE follow_ups SET {cols}, updated_ts=? WHERE contact_id=?",
                (*vals, self._now_or_zero(), contact_id))
            self._conn.commit()

    def follow_ups_by_state(self, state: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM follow_ups WHERE state=? ORDER BY updated_ts", (state,)).fetchall()
        return [dict(r) for r in rows]

    def _now_or_zero(self) -> float:
        # updated_ts служебное; точное время не критично для логики (стены меряют
        # по lead_last_ts/owner_answer_ts). 0.0 достаточно и детерминированно в тестах.
        return 0.0
```

> Примечание: `set_follow_up` пишет `updated_ts=0.0` (служебное, логика стен его не читает — она смотрит `lead_last_ts`/`owner_answer_ts`, которые задаются явно). Так тесты остаются детерминированными без инъекции часов в Store.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /c/jarvis-followup && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/chatter/test_store.py -q`
Expected: PASS (вкл. 3 новых follow_up-теста, без регрессов).

- [ ] **Step 6: Commit**

```bash
cd /c/jarvis-followup && git add chatter/storage/db.py tests/chatter/test_store.py && git commit -m "chatter(followup Ф1): таблица follow_ups + Store CRUD (register/get/set/by_state)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 1.3: триггер эскалации `return_promise` (не-suppress)

**Files:**
- Modify: `chatter/core/escalation.py` (`deterministic_escalation`, после блока `owner_handoff`)
- Test: `tests/chatter/test_escalation.py`

- [ ] **Step 1: Write the failing test**

```python
def test_return_promise_reply_escalates_without_other_trigger():
    # Чистое обещание вернуться, без keyword/промиса/владельца → карточка нужна
    # (иначе follow-up некуда регистрировать). НЕ suppress.
    r = deterministic_escalation(
        incoming_text="а когда будут свободные даты?",
        reply="Отвечу позже, как свериться с календарём.",
        knowledge=KNOWLEDGE, keywords=[], owner_id="Дмитрий")
    assert r is not None and r.tag == "return_promise"

def test_suppress_and_owner_handoff_win_over_return_promise():
    # Приоритет: suppress (unbacked_promise) > owner_handoff > return_promise.
    r1 = deterministic_escalation(
        incoming_text="скидку?", reply="Сделаю скидку, уточню и вернусь.",
        knowledge=KNOWLEDGE, keywords=[], owner_id="Дмитрий")
    assert r1 is not None and r1.tag == "unbacked_promise"
    r2 = deterministic_escalation(
        incoming_text="?", reply="Уточню и вернусь, свяжу вас с владельцем.",
        knowledge=KNOWLEDGE, keywords=[], owner_id="Дмитрий")
    assert r2 is not None and r2.tag == "owner_handoff"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/jarvis-followup && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/chatter/test_escalation.py -k return_promise -q`
Expected: FAIL (`test_return_promise...` → `r is None`).

- [ ] **Step 3: Add the trigger**

В `chatter/core/escalation.py`:
1. Вверху, к импортам, добавь: `from chatter.core.follow_up import promises_return`
2. В `deterministic_escalation`, СРАЗУ ПОСЛЕ блока `owner_handoff` (перед `return None`):

```python
    if promises_return(reply or ""):
        return EscalationReason(
            tag="return_promise", detail="обещание вернуться с ответом")
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /c/jarvis-followup && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/chatter/test_escalation.py -q`
Expected: PASS (без регрессов; owner_handoff/suppress приоритет сохранён).

- [ ] **Step 5: Commit**

```bash
cd /c/jarvis-followup && git add chatter/core/escalation.py tests/chatter/test_escalation.py && git commit -m "chatter(followup Ф1): триггер return_promise — обещание вернуться эскалирует карточку

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 1.4: строка карточки `card_followup_hint`

**Files:**
- Modify: `chatter/core/console.py` (словарь строк `console_text`)
- Test: `tests/chatter/test_console.py`

- [ ] **Step 1: Write the failing test**

```python
def test_card_followup_hint_present_ru():
    from chatter.core.console import console_text
    hint = console_text("card_followup_hint", "ru")
    assert "ответ" in hint.casefold()      # «ответь… передам лиду»
    assert hint                             # непусто
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/jarvis-followup && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/chatter/test_console.py::test_card_followup_hint_present_ru -q`
Expected: FAIL (нет ключа `card_followup_hint`).

- [ ] **Step 3: Add the string**

В `chatter/core/console.py` найди словарь локализованных строк, используемый `console_text` (там, где `card_resume_reply_hint` и подобные). Добавь ключ `card_followup_hint` во ВСЕ языковые секции, которые есть у соседних card-ключей (минимум `ru`; добавь `en`/`uk`, если у соседних ключей они есть):

```python
    # ru
    "card_followup_hint": "↩️ Ответь на эту карточку — я передам ответ лиду.",
    # en (если секция en существует у соседних card-ключей)
    "card_followup_hint": "↩️ Reply to this card — I'll pass the answer to the lead.",
    # uk (если секция uk существует)
    "card_followup_hint": "↩️ Відповідай на цю картку — я передам відповідь ліду.",
```

> Следуй ТОЧНО структуре `console.py`: если строки хранятся как `{key: {lang: text}}`, добавь `"card_followup_hint": {"ru": "...", "en": "...", "uk": "..."}`; если как отдельные per-lang словари — добавь ключ в каждый. Скопируй форму у `card_resume_reply_hint`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/jarvis-followup && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/chatter/test_console.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /c/jarvis-followup && git add chatter/core/console.py tests/chatter/test_console.py && git commit -m "chatter(followup Ф1): строка карточки card_followup_hint

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 1.5: регистрация follow-up в `_escalation_pass` + follow-up-строка в карточке (I1: лиду молчим)

**Files:**
- Modify: `chatter/run.py` (импорты; `_post_escalation_card` — параметр `followup`; `_escalation_pass` — регистрация)
- Test: `tests/chatter/test_escalation_wiring.py`

- [ ] **Step 1: Write the failing tests**

```python
from chatter.core.follow_up import promises_return  # noqa: F401 (ensures module import ok)


def test_return_promise_registers_pending_followup_when_card_delivered():
    # Аня обещает вернуться + карточка доставлена → pending follow-up заведён.
    n = FakeNotifier()
    deps = _deps(notifier=n, classify=None, keywords=[],
                 brain_reply="Отвечу позже, как свериться с календарём.")
    t = RecordingTransport()
    deps.store.get_or_create_contact("42:demo")
    process_batch("42:demo", ["когда свободные даты?"], t, deps)
    fu = deps.store.get_follow_up("42:demo")
    assert fu is not None and fu["state"] == "pending_owner"
    assert fu["topic"] == "когда свободные даты?"
    assert fu["card_ref"]                                  # ref доставленной карточки
    # I1: НИ ОДНОГО проактивного сообщения лиду — только реактивный ответ Ани.
    assert t.sent == ["Отвечу позже, как свериться с календарём."]


def test_no_followup_registered_when_card_not_delivered():
    # Карточка не дошла (I3) → follow-up не заводим (владельцу нечем ответить).
    n = _FailingNotifier()
    deps = _deps(notifier=n, classify=None, keywords=[],
                 brain_reply="Отвечу позже, дам знать.")
    t = RecordingTransport()
    deps.store.get_or_create_contact("42:demo")
    process_batch("42:demo", ["когда свободные даты?"], t, deps)
    assert deps.store.get_follow_up("42:demo") is None


def test_no_followup_when_reply_is_not_a_return_promise():
    n = FakeNotifier()
    deps = _deps(notifier=n, classify=None, keywords=[],
                 brain_reply="Съёмка стоит 15000 грн.")
    t = RecordingTransport()
    deps.store.get_or_create_contact("42:demo")
    process_batch("42:demo", ["сколько стоит съёмка?"], t, deps)
    assert deps.store.get_follow_up("42:demo") is None


def test_followup_card_carries_reply_hint():
    n = FakeNotifier()
    deps = _deps(notifier=n, classify=None, keywords=[],
                 brain_reply="Отвечу позже, дам знать.")
    t = RecordingTransport()
    deps.store.get_or_create_contact("42:demo")
    process_batch("42:demo", ["когда даты?"], t, deps)
    assert len(n.cards) == 1
    assert "передам" in n.cards[0].text_html.casefold()   # follow-up-строка в карточке
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/jarvis-followup && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/chatter/test_escalation_wiring.py -k "followup" -q`
Expected: FAIL (`get_follow_up` не заполняется; строки в карточке нет).

- [ ] **Step 3: Wire registration + card hint in `run.py`**

1. В `chatter/run.py`, к импортам добавь:

```python
import dataclasses
```
и в блок импорта из `chatter.core.follow_up`:
```python
from chatter.core.follow_up import promises_return
```

2. `_post_escalation_card` — добавь параметр `followup: bool = False` в сигнатуру; и ПОСЛЕ того как `card` собран (в обеих ветках: builder и fallback), ПЕРЕД дедуп-блоком (`active = deps.store.get_runtime_flag(...)`) вставь:

```python
        if followup:
            card = dataclasses.replace(
                card,
                text_html=card.text_html + "\n\n" + console_text("card_followup_hint", language))
```

3. В `_escalation_pass`, найди вызов `delivered = _post_escalation_card(deps, contact_id, det=det, cr=cr, now=now)` и добавь аргумент `followup`:

```python
    is_return = promises_return(reply)
    delivered = False
    if decision.escalate and deps.notifier is not None:
        delivered = _post_escalation_card(
            deps, contact_id, det=det, cr=cr, now=now, followup=is_return)
```

4. В КОНЦЕ `_escalation_pass`, ПЕРЕД `return reply` (после Q2-блока), добавь регистрацию:

```python
    # Follow-up (Фаза 1): Аня обещала вернуться И карточка ДОСТАВЛЕНА (I3) →
    # регистрируем pending follow-up. Курьер довезёт ответ владельца ПОЗЖЕ
    # (Фаза 3). ЗДЕСЬ лиду НИЧЕГО не шлём (I1) — только запись в БД.
    if delivered and is_return:
        card_ref = store.get_runtime_flag(esc_active_key(contact_id))
        if card_ref:
            store.register_follow_up(
                contact_id, topic=incoming_text, lead_last_ts=now,
                card_ref=card_ref, now=now)
    return reply
```

> `is_return` вычислен на ФИНАЛЬНОМ `reply` ДО возможной H2-замены? Нет: H2 может заменить `reply` на «уточню и вернусь к вам» (тоже промис) ЛИБО оставить. Вычисляй `is_return` ОДИН раз на реплае, который уходит лиду — то есть после Q2/H2. НО `delivered`/`_post_escalation_card` уже отработали выше. Решение: вычисли `is_return_final = promises_return(reply)` В БЛОКЕ регистрации (шаг 4 использует `promises_return(reply)` заново на финальном reply), а для `followup`-флага карточки (шаг 3) используй `is_return` на реплае ДО H2 (карточка про обещание уместна, если Аня вообще собиралась вернуться). Обнови шаг 4:

```python
    if delivered and promises_return(reply):
        card_ref = store.get_runtime_flag(esc_active_key(contact_id))
        if card_ref:
            store.register_follow_up(
                contact_id, topic=incoming_text, lead_last_ts=now,
                card_ref=card_ref, now=now)
    return reply
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /c/jarvis-followup && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/chatter/test_escalation_wiring.py -q`
Expected: PASS (вкл. 4 новых, без регрессов). **I1-тест зелёный = лиду ничего не ушло.**

- [ ] **Step 5: Full suite (регрессы + I1 по всему)**

Run: `cd /c/jarvis-followup && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/chatter/ -q`
Expected: PASS (все, включая прежние 616 + новые follow-up).

- [ ] **Step 6: Commit**

```bash
cd /c/jarvis-followup && git add chatter/run.py tests/chatter/test_escalation_wiring.py && git commit -m "chatter(followup Ф1): регистрация pending follow-up при доставленной карточке (I1: лиду молчим, I3: только при delivered)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Checkpoint (конец Фазы 1) — стоп, ручная проверка

- Все `tests/chatter/` зелёные.
- `git log --oneline` в worktree показывает 6 коммитов (Ф0 + 5 задач Ф1).
- **Ключевой инвариант I1 подтверждён тестом** `test_return_promise_registers_pending_followup_when_card_delivered` (`t.sent == [реактивный ответ]`, ноль проактива).
- Follow-up существует ТОЛЬКО в БД (`pending_owner`), лиду ничего не ушло.
- Дальше: отдельный план Фазы 2 (захват ответа владельца + режим), затем Фаза 3 (доставка + стены, первый проактив + живой дрил).

---

## Self-Review (проверка плана против спека)

- **Case A** → Task 0.1 (промпт+юнит; дрил помечен как ручной). ✅
- **promises_return + негейт** → Task 1.1. ✅
- **Таблица follow_ups (полная схема: mode, holding_sent)** → Task 1.2. ✅
- **return_promise триггер** → Task 1.3. ✅
- **Строка follow-up в карточке** → Task 1.4 + 1.5. ✅
- **Регистрация только при delivered (I3)** → Task 1.5 (`test_no_followup_registered_when_card_not_delivered`). ✅
- **I1 (ноль проактива до Фазы 3)** → Task 1.5 (`t.sent == [реактив]`). ✅
- **Не в этом плане (следующие):** захват ответа+режим (Ф2), доставка+стены+инструкция+holding+живой контекст (Ф3-5). Осознанно отложено.
- Типы/сигнатуры: `register_follow_up(contact_id, *, topic, lead_last_ts, card_ref, now)`, `get_follow_up`, `set_follow_up(**whitelist)`, `follow_ups_by_state(state)`, `promises_return(reply)`, `_post_escalation_card(..., followup=False)` — согласованы между задачами. ✅
