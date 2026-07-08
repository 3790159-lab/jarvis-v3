# Brain `run_command` Bridge (Вариант B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Дать LLM-роутеру («Brain») инструмент `run_command(cmd, args)` — мост в существующий дисп­атч команд с ПОЛНЫМ сохранением ролевого гейта и confirm-на-платных, так что Brain знает про команды (реестр в промпте) и может их вызывать, но НЕ может обойти деньги/роли через этот инструмент.

**Architecture:** Brain остаётся ассистентом (веб-ресёрч, генерация, диалог) — добавляем ОДИН новый tool. Гейт живёт на границе исполнения (bridge-функция + callback), ключуется по `(роль из chat_id, cmd)` и НИКОГДА не доверяет тексту/рассуждению Brain. Платная команда → confirm-кнопка (не exec); free+read-only → `handle()` (тот же ролевой гейт, что при слэше). Реестр команд инжектится в системный промпт роутера, роль-фильтрованно.

**Tech Stack:** Python 3.14, Anthropic tool-use (`app/services/unified/llm_router`), existing `handle()`/`handle_command`/`send_with_keyboard`/`handle_callback_query` в control-файле, pytest + мутационные «зубы» в обе стороны.

---

## Ground truth (проверено чтением running-кода — читать перед ревью)

- **«Brain» = unified `LLMRouter`** (`JARVIS_ROUTER_ENABLED=1`, model `claude-sonnet-4-6`). Перехватывает весь non-slash текст в `_run_router` (`control:8265`, gate `8273`: enabled + не-`/` → `route_message` `8297`); legacy `handle()` — только фолбэк при None. Slash-текст всегда идёт в `handle()` (`8275`).
- **Tool-модель:** `Tool(name, description, input_schema, handler)`, `handler: async (params, context) -> ToolResult` (`tool_registry.py`). `ToolContext(user_id, username, chat_id, conversation_state)`. `ToolResult.ok_text/fail/photo/video`. Инструменты собираются в `register_default_tools(registry, *, ..._fn=...)` (`tools/__init__.py`), бэкенды инжектятся мостами из control-файла в `_build_router` (`control:8218`).
- **Ролевой гейт живёт в `handle()`** (`control:7516`): `role = _role_for_chat(chat_id)` (+ back-compat `ALLOWED_CHAT_ID`→admin); friend → `_cmd in FRIEND_ALLOWED_COMMANDS` иначе «🚫 Эта команда доступна только администратору». **`handle_command` (`6386`) гейта НЕ содержит** — вызывается уже после гейта в `handle()`. ⚠️ Значит мост ДОЛЖЕН идти через `handle()`, а не напрямую в `handle_command`, иначе гейт обходится.
- **Confirm/callback инфра:** `send_with_keyboard(chat_id, text, inline_keyboard)->msg_id` (`259`); `handle_callback_query` (`4304`) с friend-гардом `if not data.startswith(FRIEND_ALLOWED_CALLBACK_PREFIXES): 🚫` (`4324`); `answer_callback_query` (`228`); pending через `state[...]` + `load_state`/`save_state`. `FRIEND_ALLOWED_CALLBACK_PREFIXES` (`7689`).
- **is_paid/price_hint/auto_exec_ok/PAID/FREE_AUTOEXEC/build_corpus** живут в `tools/intent_router.py` — **чистый модуль, БЕЗ wiring в classify_message** (wiring — отдельные коммиты, они нам НЕ нужны: роутер шунтирует classify). Модуль автономен, 18 unit-тестов (`tests/test_intent_router.py`).
- **⚠️ Коррекция премисы:** реестр команд СЕЙЧАС в промпте Brain ОТСУТСТВУЕТ (промпт = хардкод `_DEFAULT_SYSTEM_PROMPT` `router.py:33` + curated tool_registry; grep пакета по menu/registry/health/browse = пусто). Поэтому Ф1 (инжект реестра) — обязательная часть этой арки, не «уже сделано».

---

## Модель безопасности (сердце ревью)

**Инвариант:** команда исполняется ⇔ `(роль, cmd)` проходит гейт, где `роль = _role_for_chat(chat_id)` резолвится НЕЗАВИСИМО от Brain. Bridge-функция и callback НЕ читают свободный текст и НЕ доверяют аргументам/рассуждению модели — только `cmd` (строка) + `chat_id` (из `ToolContext`, приходит из Telegram-update, не из модели).

- **Инъекция невозможна конструктивно:** сколько бы юзер-friend ни «уговаривал» Brain, tool-хендлер получает `context.chat_id` (реальный отправитель) → `_role_for_chat` → если friend и `cmd ∉ FRIEND_ALLOWED_COMMANDS` → отказ. Brain физически не может подменить chat_id или роль — они не в его inputs.
- **Гейт дублируется на callback:** нажатие `rc:run` повторно резолвит роль и проверяет friend-allowed для pending-команды. Callback НЕ доверяет факту, что кнопка отрисована — роль берётся заново из отправителя апдейта (friend не подтвердит admin-команду, даже если кнопка как-то оказалась у него).

### 🔑 Двухключевой инвариант (оборона в глубину — ОБЯЗАТЕЛЬНОЕ дополнение)

**Деньги держат ДВА независимых слоя, не одна дверь.** Обойти надо ОБА одновременно, а слой 2 живёт в коде команды — куда `run_command`/Brain не дотягиваются:
- **Слой 1 (run_command):** не авто-экзекает не-allowlist и платное — платное уходит в confirm-кнопку.
- **Слой 2 (внутри самой команды):** даже если слой 1 пробит (баг / инъекция / прямой вызов в обход allowlist) — `guard_spend`/`check_limit` ВНУТРИ хендлера всё равно на пути и режет по лимиту. Проверено фактом: `/menu_photo` → `guard_spend` (`control:6156/6196`), `/browse_check` → `guard_spend` (`control:1559`, до спавна треда), `spend_guard.py`: `check_limit` строго ДО траты, `do_spend()` вызывается ТОЛЬКО если allowed.

**Гарантия:** Brain не может вызвать трату мимо лимита ни одним путём. Зуб — **Task 3.3**: прямой вызов платной команды в обход allowlist `run_command` → `guard_spend` всё равно на пути и режет; мутация (снять внутренний гейт) → RED.

---

## File Structure

- **Create:** `app/services/unified/llm_router/tools/run_command.py` — фабрика `build_run_command_tool(*, run_command_fn=None)`; хендлер только парсит `cmd`/`args` и зовёт инжектированный `run_command_fn`. Ноль Telegram/ролевой логики в пакете роутера (тестируемо на моках).
- **Create:** `tools/intent_router.py` — перенос ЧИСТОГО модуля из ветки `intent-router` (без wiring). Даёт `PAID`/`FREE_AUTOEXEC`/`is_paid`/`price_hint`/`auto_exec_ok`/`build_corpus`.
- **Modify:** `app/services/unified/llm_router/tools/__init__.py` — импорт+регистрация `run_command` в `register_default_tools` (новый kwarg `run_command_fn`).
- **Modify:** `app/services/unified/llm_router/router.py` — приём роль-фильтрованного реестра в промпт (новый параметр/аппенд).
- **Modify:** `tools/jarvis_smart_telegram_control.py`:
  - `_router_run_command(cmd, args, chat_id, user_id)` — bridge (роль-гейт + paid-confirm + free-exec).
  - `_router_command_registry_text(role)` — компактный роль-фильтрованный список для промпта.
  - `_build_router` (`8218`): прокинуть `run_command_fn=_router_run_command` + реестр в промпт.
  - `handle_callback_query` (`4304`): ветка `rc:run`/`rc:cancel` с повторным гейтом.
  - `FRIEND_ALLOWED_CALLBACK_PREFIXES` (`7689`): + `"rc:"`.
- **Create tests:** `tests/test_run_command_tool.py` (пакет-уровень, моки), `tests/test_router_run_command_bridge.py` (bridge+callback+зубы в control-файле).

---

## Ф0 — Перенести чистый `intent_router.py` в прод

### Task 0.1: Скопировать модуль + unit-тесты (без wiring)

**Files:**
- Create: `tools/intent_router.py`, `tests/test_intent_router.py`

- [ ] **Step 1: Достать чистый модуль из ветки intent-router**

Run:
```bash
cd /c/jarvis
git show intent-router:tools/intent_router.py > tools/intent_router.py
git show intent-router:tests/test_intent_router.py > tests/test_intent_router.py
```
Expected: два файла созданы. НЕ трогаем `jarvis_smart_telegram_control.py` (wiring не переносим).

- [ ] **Step 2: Прогнать unit-тесты модуля**

Run: `python -m pytest tests/test_intent_router.py -q -p no:cacheprovider`
Expected: `18 passed`.

- [ ] **Step 3: Проверить, что импорт чистый (нет зависимости на control-файл)**

Run: `python -c "import tools.intent_router as ir; print(ir.is_paid('/menu_photo'), ir.auto_exec_ok('/health'), len(ir.build_corpus()))"`
Expected: печатает `True/False`-значения и число команд (≈110) без ImportError.

- [ ] **Step 4: Commit**

```bash
git add tools/intent_router.py tests/test_intent_router.py
git commit -m "feat(ir): land pure intent_router module (paid/free classification + corpus) — no wiring"
```

---

## Ф1 — Инжект роль-фильтрованного реестра в промпт Brain

### Task 1.1: Тест — реестр admin содержит команды, friend — без admin-команд

**Files:**
- Test: `tests/test_router_run_command_bridge.py`

- [ ] **Step 1: Написать падающий тест**

```python
def test_command_registry_text_role_filtered():
    import tools.jarvis_smart_telegram_control as bot
    admin_txt = bot._router_command_registry_text("admin")
    friend_txt = bot._router_command_registry_text("friend")
    # admin видит admin-команду
    assert "/git_status" in admin_txt
    # friend НЕ видит admin-only команду в промпте (defense-in-depth)
    assert "/git_status" not in friend_txt
    # friend видит хотя бы одну свою
    assert "/menu_photo" in friend_txt
```

- [ ] **Step 2: Прогнать — FAIL (нет функции)**

Run: `python -m pytest tests/test_router_run_command_bridge.py::test_command_registry_text_role_filtered -v`
Expected: FAIL — `AttributeError: _router_command_registry_text`.

- [ ] **Step 3: Реализовать `_router_command_registry_text` в control-файле**

Рядом с `_router_file_context_hint` (`control:~8144`):

```python
def _router_command_registry_text(role: str) -> str:
    """Компактный роль-фильтрованный список команд для системного промпта Brain.

    Defense-in-depth: friend вообще не видит admin-команды в промпте (реальный
    гейт — в _router_run_command). Источник — реестр меню; для friend оставляем
    только FRIEND_ALLOWED_COMMANDS.
    """
    from tools import jarvis_menu as jmenu
    lines = []
    for item in jmenu.iter_items():              # (cmd, label) по реестру
        cmd = item.cmd
        if role != "admin" and cmd not in FRIEND_ALLOWED_COMMANDS:
            continue
        label = (item.label or "").strip()
        lines.append(f"{cmd} — {label}" if label else cmd)
    header = (
        "\n\nДОСТУПНЫЕ КОМАНДЫ (вызывай инструментом run_command, "
        "передавая точное имя со слешем в поле cmd; аргументы — в args):\n"
    )
    return header + "\n".join(lines)
```

> Если в `jarvis_menu` нет `iter_items()` — использовать существующий обход `MENU` (список категорий → `item.cmd/.label`); см. `jarvis_menu.py` (реестр). Проверить точное имя в Task-реализации, НЕ выдумывать.

- [ ] **Step 4: Прогнать — PASS**

Run: `python -m pytest tests/test_router_run_command_bridge.py::test_command_registry_text_role_filtered -v`
Expected: PASS.

### Task 1.2: Прокинуть реестр в системный промпт роутера

**Files:**
- Modify: `app/services/unified/llm_router/router.py` (приём доп. статик-контекста в промпт)
- Modify: `tools/jarvis_smart_telegram_control.py` `_run_router` (`8294`): добавить реестр к `extra_context`

- [ ] **Step 1: Тест — extra_context с реестром доходит до system-prompt**

```python
def test_router_prompt_includes_registry(monkeypatch):
    from app.services.unified.llm_router.router import LLMRouter
    captured = {}
    class _FakeClient:
        class messages:
            @staticmethod
            def create(**kw):
                captured.update(kw)
                class R:  # noqa
                    content = []; stop_reason = "end_turn"; usage = None
                return R()
    r = LLMRouter(_FakeClient(), __import__("app.services.unified.llm_router.tool_registry", fromlist=["ToolRegistry"]).ToolRegistry(), model="claude-sonnet-4-6")
    import asyncio
    asyncio.run(r.route_message("привет", _ctx(), extra_context="\n\nДОСТУПНЫЕ КОМАНДЫ:\n/health — здоровье"))
    assert "/health" in captured["system"]
```

- [ ] **Step 2: Прогнать — FAIL если extra_context не аппендится в system**

Run: `python -m pytest tests/test_router_run_command_bridge.py::test_router_prompt_includes_registry -v`
Expected: PASS уже сейчас, если `route_message` аппендит extra_context (`router.py:217`) — тогда это регресс-страховка. Если FAIL — доработать аппенд.

- [ ] **Step 3: В `_run_router` добавить реестр в extra_context**

`control:8294` — рядом с `extra_context = _router_file_context_hint(load_state())`:

```python
    _role = _role_for_chat(chat_id) or ("admin" if str(chat_id) == ALLOWED_CHAT_ID else "friend")
    _reg = _router_command_registry_text(_role)
    _file_hint = _router_file_context_hint(load_state())
    extra_context = "\n\n".join(x for x in (_file_hint, _reg) if x)
```

- [ ] **Step 4: Прогнать оба теста + commit**

Run: `python -m pytest tests/test_router_run_command_bridge.py -q`
```bash
git add app/services/unified/llm_router/router.py tools/jarvis_smart_telegram_control.py tests/test_router_run_command_bridge.py
git commit -m "feat(router): inject role-filtered command registry into Brain system prompt"
```

---

## Ф2 — `run_command` tool + bridge (роль-гейт + paid-confirm)

### Task 2.1: Пакет-уровень tool (моки, ноль Telegram-логики)

**Files:**
- Create: `app/services/unified/llm_router/tools/run_command.py`
- Test: `tests/test_run_command_tool.py`

- [ ] **Step 1: Тест — хендлер зовёт инжектированный run_command_fn с (cmd,args,chat_id,user_id)**

```python
import asyncio
from app.services.unified.llm_router.tool_registry import ToolContext
from app.services.unified.llm_router.tools.run_command import build_run_command_tool

def test_run_command_delegates_to_injected_fn():
    seen = {}
    def fake_fn(cmd, args, chat_id, user_id):
        seen.update(cmd=cmd, args=args, chat_id=chat_id, user_id=user_id)
        return "ок, отправил подтверждение"
    tool = build_run_command_tool(run_command_fn=fake_fn)
    ctx = ToolContext(user_id=42, username="u", chat_id="100")
    res = asyncio.run(tool.handler({"cmd": "/menu_photo", "args": "борщ"}, ctx))
    assert seen == {"cmd": "/menu_photo", "args": "борщ", "chat_id": "100", "user_id": 42}
    assert res.kind == "text" and "подтвержд" in res.text
```

- [ ] **Step 2: Прогнать — FAIL (нет модуля)**

Run: `python -m pytest tests/test_run_command_tool.py::test_run_command_delegates_to_injected_fn -v`
Expected: FAIL (ModuleNotFound).

- [ ] **Step 3: Реализовать фабрику**

```python
# -*- coding: utf-8 -*-
"""``run_command`` tool — bridge Brain into the bot's role-gated command dispatch.

The actual role/money/confirm logic lives in the injected ``run_command_fn``
(supplied by the bot bridge); this module only parses params and reports the
outcome string back to Claude. No Telegram or gating logic here (testable).
"""
from __future__ import annotations

import inspect
from typing import Any, Callable, Optional

from app.services.unified.llm_router.tool_registry import Tool, ToolContext, ToolResult

# run_command_fn: (cmd, args, chat_id, user_id) -> str (human-readable outcome)
RunCommandFn = Callable[[str, str, str, Optional[int]], Any]


async def _maybe_await(v: Any) -> Any:
    return await v if inspect.isawaitable(v) else v


def build_run_command_tool(*, run_command_fn: Optional[RunCommandFn] = None) -> Tool:
    async def handler(params: dict, context: ToolContext) -> ToolResult:
        cmd = str(params.get("cmd") or "").strip()
        args = str(params.get("args") or "").strip()
        if not cmd.startswith("/"):
            return ToolResult.fail("cmd должен быть командой со слешем, напр. /health")
        if run_command_fn is None:
            return ToolResult.fail("run_command не подключён в этом окружении.")
        try:
            outcome = await _maybe_await(
                run_command_fn(cmd, args, context.chat_id, context.user_id)
            )
        except Exception as exc:  # noqa: BLE001 - graceful tool error
            return ToolResult.fail(f"Команда не выполнена: {exc}")
        return ToolResult.ok_text(str(outcome))

    return Tool(
        name="run_command",
        description=(
            "Выполнить команду бота из списка ДОСТУПНЫЕ КОМАНДЫ (см. системный "
            "промпт). Передавай точное имя со слешем в cmd и аргументы в args. "
            "ВАЖНО: платные команды не выполнятся сразу — пользователю придёт "
            "кнопка подтверждения; сообщи об этом. Команды не из списка/не по "
            "правам будут отклонены. Используй, когда пользователь просит "
            "действие, которое делает конкретная команда (напр. «глянь что с "
            "ботом» → /health, «проверь сайт X» → /browse_check X)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "cmd": {"type": "string", "description": "Точное имя команды со слешем."},
                "args": {"type": "string", "description": "Аргументы (может быть пусто)."},
            },
            "required": ["cmd"],
        },
        handler=handler,
    )
```

- [ ] **Step 4: Прогнать — PASS + зарегистрировать**

Run: `python -m pytest tests/test_run_command_tool.py -v`
В `tools/__init__.py`: импорт `build_run_command_tool`, новый kwarg `run_command_fn`, `registry.register(build_run_command_tool(run_command_fn=run_command_fn))`, добавить в `__all__`.

- [ ] **Step 5: Commit**

```bash
git add app/services/unified/llm_router/tools/run_command.py app/services/unified/llm_router/tools/__init__.py tests/test_run_command_tool.py
git commit -m "feat(router): run_command tool (delegates to injected bridge)"
```

### Task 2.2: Bridge `_router_run_command` — ЗУБ роль-гейта (friend→admin-cmd отказ)

**Files:**
- Modify: `tools/jarvis_smart_telegram_control.py`
- Test: `tests/test_router_run_command_bridge.py`

- [ ] **Step 1: Тест-зуб (инъекция/роль): friend + admin-команда → отказ, handle НЕ вызван**

```python
def test_bridge_friend_denied_admin_command(monkeypatch):
    import tools.jarvis_smart_telegram_control as bot
    monkeypatch.setattr(bot, "_role_for_chat", lambda c: "friend")
    called = {"handle": False, "kb": False}
    monkeypatch.setattr(bot, "handle", lambda *a, **k: called.__setitem__("handle", True))
    monkeypatch.setattr(bot, "send_with_keyboard", lambda *a, **k: called.__setitem__("kb", True))
    out = bot._router_run_command("/git_status", "", "555", 555)
    assert "🚫" in out
    assert called == {"handle": False, "kb": False}   # ни exec, ни confirm
```

- [ ] **Step 2: Прогнать — FAIL (нет функции)**

Run: `python -m pytest tests/test_router_run_command_bridge.py::test_bridge_friend_denied_admin_command -v`
Expected: FAIL.

- [ ] **Step 3: Реализовать bridge (роль-гейт + paid-confirm + free-exec)**

Рядом с прочими `_router_*_backend` (`control:~8090`):

```python
def _router_run_command(cmd: str, args: str, chat_id: str, user_id: Optional[int]) -> str:
    """Мост Brain→команды. Гейт по (роль из chat_id, cmd) — НЕ доверяем модели.

    friend+не-разрешённое → отказ; платное → confirm-кнопка (не exec);
    free+auto-exec → handle() (тот же ролевой гейт, что при слэше).
    """
    from tools import intent_router as _ir
    cmd = (cmd or "").strip()
    args = (args or "").strip()

    # 1) роль — независимо от Brain
    role = _role_for_chat(chat_id)
    if role is None and str(chat_id) == ALLOWED_CHAT_ID:
        role = "admin"
    if role is None:
        return "🚫 Доступ запрещён."

    # 2) существование команды в реестре
    if cmd not in _ir.build_corpus():
        return f"🚫 Неизвестная команда: {cmd}. Открой /menu."

    # 3) ролевой гейт — ТОТ ЖЕ, что при слэше (default-deny)
    if role != "admin" and cmd not in FRIEND_ALLOWED_COMMANDS:
        return "🚫 Эта команда доступна только администратору."

    cmd_text = (cmd + (" " + args if args else "")).strip()

    # 4) платное/параметрическое → confirm-кнопка, НЕ exec
    if _ir.is_paid(cmd) or not _ir.auto_exec_ok(cmd):
        st = load_state()
        st["pending_rc"] = {"cmd": cmd, "args": args, "chat_id": str(chat_id)}
        save_state(st)
        price = _ir.price_hint(cmd)
        ptxt = f" (платно ~${price:.2f})" if price else ""
        send_with_keyboard(
            str(chat_id), f"Запустить {cmd_text}?{ptxt}",
            [[{"text": f"▶️ Запустить {cmd}", "callback_data": "rc:run"},
              {"text": "Отмена", "callback_data": "rc:cancel"}]],
        )
        return f"Отправил кнопку подтверждения для {cmd_text} — жду нажатия."

    # 5) free + read-only → exec через handle() (role re-gated внутри)
    handle(str(chat_id), cmd_text)
    return f"Выполнил {cmd_text}."
```

- [ ] **Step 4: Прогнать — PASS**

Run: `python -m pytest tests/test_router_run_command_bridge.py::test_bridge_friend_denied_admin_command -v`

### Task 2.3: ЗУБ paid-confirm (платное → кнопка, не exec) + free-exec

**Files:**
- Test: `tests/test_router_run_command_bridge.py`

- [ ] **Step 1: Два теста**

```python
def test_bridge_paid_sends_confirm_not_exec(monkeypatch):
    import tools.jarvis_smart_telegram_control as bot
    monkeypatch.setattr(bot, "_role_for_chat", lambda c: "admin")
    kb = {}; monkeypatch.setattr(bot, "send_with_keyboard",
                                 lambda cid, txt, rows: kb.update(txt=txt, rows=rows) or 1)
    ex = {"n": 0}; monkeypatch.setattr(bot, "handle", lambda *a, **k: ex.__setitem__("n", ex["n"] + 1))
    monkeypatch.setattr(bot, "save_state", lambda s: None)
    monkeypatch.setattr(bot, "load_state", lambda: {})
    out = bot._router_run_command("/menu_photo", "борщ", "1", 1)   # платная
    assert ex["n"] == 0                       # НЕ exec
    assert "rc:run" in str(kb["rows"])        # кнопка подтверждения
    assert "подтвержд" in out.lower()

def test_bridge_free_execs_via_handle(monkeypatch):
    import tools.jarvis_smart_telegram_control as bot
    monkeypatch.setattr(bot, "_role_for_chat", lambda c: "admin")
    seen = {}; monkeypatch.setattr(bot, "handle", lambda cid, txt: seen.update(cid=cid, txt=txt))
    kb = {"n": 0}; monkeypatch.setattr(bot, "send_with_keyboard",
                                       lambda *a, **k: kb.__setitem__("n", kb["n"] + 1))
    out = bot._router_run_command("/health", "", "1", 1)          # free read-only
    assert seen == {"cid": "1", "txt": "/health"}
    assert kb["n"] == 0                        # без кнопки
    assert "Выполнил" in out
```

- [ ] **Step 2: Прогнать — PASS**

Run: `python -m pytest tests/test_router_run_command_bridge.py -k "paid_sends_confirm or free_execs" -v`

- [ ] **Step 3: Мутационная проверка (обе стороны)**

Вручную (без коммита): в bridge закомментировать блок «4) платное → confirm» (сразу exec).
Run: `python -m pytest tests/test_router_run_command_bridge.py::test_bridge_paid_sends_confirm_not_exec -v` → Expected: **FAIL** (handle вызван). Откатить.
Затем ослабить «3) ролевой гейт» (убрать условие).
Run: `...::test_bridge_friend_denied_admin_command -v` → Expected: **FAIL**. Откатить.

- [ ] **Step 4: Прокинуть bridge в роутер + commit**

`_build_router` (`control:8218`) — добавить в `register_default_tools(...)`: `run_command_fn=_router_run_command`.
```bash
git add tools/jarvis_smart_telegram_control.py tests/test_router_run_command_bridge.py
git commit -m "feat(router): run_command bridge — role gate + paid-confirm, wired into Brain"
```

---

## Ф3 — Callback подтверждения `rc:` (повторный гейт)

### Task 3.1: ЗУБ callback-роли (friend не подтвердит admin-команду)

**Files:**
- Modify: `tools/jarvis_smart_telegram_control.py` (`handle_callback_query` `4304`, `FRIEND_ALLOWED_CALLBACK_PREFIXES` `7689`)
- Test: `tests/test_router_run_command_bridge.py`

- [ ] **Step 1: Тест-зуб**

```python
def test_rc_callback_friend_cannot_confirm_admin(monkeypatch):
    import tools.jarvis_smart_telegram_control as bot
    monkeypatch.setattr(bot, "_role_for_chat", lambda c: "friend")
    ex = {"n": 0}; monkeypatch.setattr(bot, "handle", lambda *a, **k: ex.__setitem__("n", ex["n"] + 1))
    monkeypatch.setattr(bot, "answer_callback_query", lambda *a, **k: None)
    state = {"pending_rc": {"cmd": "/git_status", "args": "", "chat_id": "555"}}
    cq = {"id": "cq1", "data": "rc:run", "message": {"chat": {"id": 555}}, "from": {"id": 555}}
    bot.handle_callback_query(cq, state)
    assert ex["n"] == 0                        # admin-команда НЕ исполнена для friend
```

- [ ] **Step 2: Прогнать — FAIL (нет ветки rc:)**

Run: `python -m pytest tests/test_router_run_command_bridge.py::test_rc_callback_friend_cannot_confirm_admin -v`

- [ ] **Step 3: Добавить ветку `rc:` в `handle_callback_query` + префикс**

`FRIEND_ALLOWED_CALLBACK_PREFIXES` (`7689`): добавить `"rc:"`.
В `handle_callback_query` (после friend-гарда `4324`), ветка:

```python
    if data == "rc:cancel":
        state.pop("pending_rc", None); save_state(state)
        answer_callback_query(cq_id, "Отменено")
        return
    if data == "rc:run":
        pend = (state or {}).get("pending_rc") or {}
        cmd = pend.get("cmd", ""); args = pend.get("args", "")
        # повторный гейт — роль резолвится заново из отправителя
        role = _role_for_chat(chat_id)
        if role is None and str(chat_id) == ALLOWED_CHAT_ID:
            role = "admin"
        if role is None or (role != "admin" and cmd not in FRIEND_ALLOWED_COMMANDS):
            answer_callback_query(cq_id, "🚫 Недоступно")
            state.pop("pending_rc", None); save_state(state)
            return
        state.pop("pending_rc", None); save_state(state)
        answer_callback_query(cq_id, "▶️")
        handle(str(chat_id), (cmd + (" " + args if args else "")).strip())  # money-гейт внутри
        return
```

- [ ] **Step 4: Прогнать — PASS + admin-путь тест**

```python
def test_rc_callback_admin_confirms_and_execs(monkeypatch):
    import tools.jarvis_smart_telegram_control as bot
    monkeypatch.setattr(bot, "_role_for_chat", lambda c: "admin")
    seen = {}; monkeypatch.setattr(bot, "handle", lambda cid, txt: seen.update(cid=cid, txt=txt))
    monkeypatch.setattr(bot, "answer_callback_query", lambda *a, **k: None)
    monkeypatch.setattr(bot, "save_state", lambda s: None)
    state = {"pending_rc": {"cmd": "/menu_photo", "args": "борщ", "chat_id": "1"}}
    cq = {"id": "c", "data": "rc:run", "message": {"chat": {"id": 1}}, "from": {"id": 1}}
    bot.handle_callback_query(cq, state)
    assert seen == {"cid": "1", "txt": "/menu_photo борщ"}
```

Run: `python -m pytest tests/test_router_run_command_bridge.py -k rc_callback -v`

- [ ] **Step 5: Мутация callback-гейта**

Убрать условие `role != "admin" and cmd not in FRIEND_ALLOWED_COMMANDS`.
Run: `...::test_rc_callback_friend_cannot_confirm_admin -v` → Expected: **FAIL**. Откатить.

- [ ] **Step 6: Commit**

```bash
git add tools/jarvis_smart_telegram_control.py tests/test_router_run_command_bridge.py
git commit -m "feat(router): rc: confirm callback with re-checked role gate"
```

### Task 3.2: ЗУБ «диалоговость не сломана» + полный регресс-гейт

**Files:**
- Test: `tests/test_router_run_command_bridge.py`

- [ ] **Step 1: Тест — не-командный текст НЕ трогает run_command**

```python
def test_dialog_preserved_non_command(monkeypatch):
    # web_research / чат по-прежнему доступны: run_command — лишь ОДИН из инструментов,
    # Brain не обязан его звать. Проверяем, что реестр tools содержит и run_command,
    # и web_research (диалоговые инструменты на месте).
    import app.services.unified.llm_router.tools as T
    from app.services.unified.llm_router.tool_registry import ToolRegistry
    reg = ToolRegistry()
    T.register_default_tools(reg, run_command_fn=lambda *a: "ok")
    names = reg.names()
    assert "run_command" in names and "web_research" in names and "get_user_stats" in names
```

- [ ] **Step 2: Прогнать все новые тесты**

Run: `python -m pytest tests/test_run_command_tool.py tests/test_router_run_command_bridge.py tests/test_intent_router.py -q -p no:cacheprovider`
Expected: все зелёные.

### Task 3.3: 🔑 ЗУБ двухключевого инварианта (слой-2 держит, даже если слой-1 пробит)

**Files:**
- Test: `tests/test_router_run_command_bridge.py`

- [ ] **Step 1: Тест — прямой вызов платной в обход allowlist run_command → guard_spend всё равно режет**

```python
def test_paid_command_gated_internally_when_run_command_bypassed(monkeypatch):
    """Оборона в глубину: даже если слой-1 (run_command allowlist/confirm) обойдён
    и платная команда дёрнута напрямую — guard_spend ВНУТРИ команды на пути и
    режет по лимиту. do_spend не вызывается при deny."""
    import tools.jarvis_smart_telegram_control as bot
    calls = {"guard": 0, "did_spend": 0}

    def fake_guard(chat_id, username, est, do_spend):
        calls["guard"] += 1
        # DENY: over-limit — do_spend НЕ вызывается (деньги не тратятся)
        return (None, "🚫 Лимит исчерпан")

    monkeypatch.setattr(bot, "guard_spend", fake_guard)
    monkeypatch.setattr(bot, "_role_for_chat", lambda c: "friend")
    sent = []
    monkeypatch.setattr(bot, "send", lambda cid, txt, *a, **k: sent.append(txt))

    # Прямой вызов платной команды В ОБХОД run_command (симуляция пробитого слоя-1):
    bot.handle("100", "/menu_photo борщ")

    assert calls["guard"] >= 1                       # слой-2 реально на пути
    assert calls["did_spend"] == 0                   # трата не случилась
    assert any(("лимит" in s.lower()) or ("🚫" in s) for s in sent)   # порезал
```

> Реализатору: проверить, что платный путь `/menu_photo` для friend действительно проходит через `guard_spend` (`control:6156/6196`). Если конкретный платный хендлер, который зовёт `run_command`, использует `_check_limit` напрямую (не `guard_spend`) — мокать соответствующий гейт. Суть зуба: платная команда НЕ тратит при deny, независимо от `run_command`.

- [ ] **Step 2: Прогнать — PASS (слой-2 уже существует в проде)**

Run: `python -m pytest tests/test_router_run_command_bridge.py::test_paid_command_gated_internally_when_run_command_bypassed -v`
Expected: PASS — `guard_spend` уже в пути платных команд; зуб фиксирует инвариант, чтобы будущая правка его не сняла.

- [ ] **Step 3: Мутация — снять внутренний гейт → RED**

Вручную (без коммита): в платном хендлере `/menu_photo` (`control:6156`) заменить `guard_spend(chat_id, None, est, _do)` на прямой `_do()` (обход слоя-2).
Run: `...::test_paid_command_gated_internally_when_run_command_bypassed -v`
Expected: **FAIL** (`guard` count = 0, трата прошла). Откатить мутацию → снова PASS. Это доказывает, что оба слоя независимы и зуб ловит потерю слоя-2.

- [ ] **Step 4: Commit**

```bash
git add tests/test_router_run_command_bridge.py
git commit -m "test(router): defense-in-depth tooth — internal guard_spend holds even if run_command bypassed"
```

### Task 3.4: Полный регресс-гейт

**Files:**
- Worktree

- [ ] **Step 1: Прогнать все новые тесты**

Run: `python -m pytest tests/test_run_command_tool.py tests/test_router_run_command_bridge.py tests/test_intent_router.py -q -p no:cacheprovider`
Expected: все зелёные.

- [ ] **Step 2: Полный регресс-гейт**

Run:
```bash
python -m pytest tests/ -q -p no:cacheprovider --continue-on-collection-errors --tb=no 2>&1 | tail -3
```
Expected: `failed` ≤ baseline (± flak bolt/figma/landing), **NEW=0**. При NEW>0 — ID-diff, solo-прогон, при реальном регрессе СТОП.

---

## Живой прогон (после ОК на мердж — НЕ часть кода)

- «глянь что с ботом» → Brain зовёт `run_command("/health")` → free → выполнил, показал health.
- «проверь сайт example.com» → `run_command("/browse_check","example.com")` → платно → **кнопка подтверждения**; нажать → выполнилось; money-гейт списал.
- friend-аккаунт: «покажи git статус» → Brain пытается `/git_status` → **«🚫 только админ»** (bridge-гейт), даже если переформулировать/уговаривать.
- «сделай ресёрч про X» → web_research как раньше (диалоговость цела).
- «сгенерируй картинку кота» → generate_image как раньше (Brain не обязан звать run_command).

---

## Зубы (сводка — все мутациями в обе стороны, на моках)

| # | Требование | Тест | Мутация → RED |
|---|---|---|---|
| 1 | платное → confirm, не exec | `test_bridge_paid_sends_confirm_not_exec` | убрать paid-ветку |
| 2 | friend → только его команды | `test_bridge_friend_denied_admin_command` | убрать ролевой гейт |
| 3 | инъекция бессильна (гейт по chat_id, не по тексту) | тот же #2 + `test_rc_callback_friend_cannot_confirm_admin` | убрать callback-гейт |
| 🔑 | **двухключевой: слой-2 держит при пробитом слое-1** | `test_paid_command_gated_internally_when_run_command_bypassed` | снять `guard_spend` в хендлере |
| 4 | callback не доверяет отрисованной кнопке (роль заново) | `test_rc_callback_friend_cannot_confirm_admin` | убрать callback-гейт |
| 5 | диалоговость цела | `test_dialog_preserved_non_command` | — (страховка набора инструментов) |
| — | free → exec без кнопки | `test_bridge_free_execs_via_handle` | — |
| — | admin confirm → exec | `test_rc_callback_admin_confirms_and_execs` | — |

---

## Self-Review

**1. Spec coverage vs требования:**
- «платное → confirm-кнопка, не exec» → Task 2.3 зуб #1 + Ф3 callback ✅
- «friend → только разрешённые (тот же гейт)» → bridge п.3 через `FRIEND_ALLOWED_COMMANDS` + `handle()` re-gate; Task 2.2 зуб #2 ✅
- «инъекция бессильна» → гейт по `_role_for_chat(chat_id)`, модель не влияет; зуб #3 (bridge+callback) ✅
- «двухключевой инвариант / оборона в глубину» → слой-2 (`guard_spend` внутри команды) независим от run_command; Task 3.3 зуб 🔑 + мутация снятия гейта ✅
- «callback не доверяет отрисованной кнопке» → Ф3 Task 3.1 повторный резолв роли из отправителя; зуб #4 ✅
- «диалоговость не ломаем» → run_command — доп. инструмент, web_research/generate/chat нетронуты; зуб #4 ✅
- «реестр в промпте» (премиса ложна → делаем) → Ф1 ✅

**2. Placeholder scan:** код приведён дословно; единственная явно помеченная проверка-по-месту — точное имя обхода реестра в `jarvis_menu` (Task 1.1 Step 3, «проверить, не выдумывать»). Не placeholder — инструкция сверить существующий API. ✅

**3. Type consistency:** `run_command_fn(cmd, args, chat_id, user_id)` — одна сигнатура в tool (Task 2.1), bridge (Task 2.2), тестах. `pending_rc={cmd,args,chat_id}` — один ключ в bridge (2.2) и callback (3.1). `is_paid/auto_exec_ok/price_hint/build_corpus` — контракт `intent_router` (Ф0). ✅

**Явно вне области:** реордер classify_message / IR-роутер (шунтируется Brain — не нужен); IR-2 Haiku; тюнинг description'ов Brain под лучшее распознавание команд (итеративно после живого прогона).

---

## Рамки

- Тесты только на моках (реальный Anthropic/Telegram = СТОП). Кода не писать до ОК.
- Гейт ВСЕГДА по роли из `chat_id`, НИКОГДА по тексту/аргументам модели.
- Мост идёт через `handle()` (re-gate), НЕ напрямую в `handle_command`.
- Money-гейт (`check_limit`/`guard_spend`) сохраняется как второй слой — confirm его не заменяет.
- НЕ мерджить, НЕ рестартить бота до ОК. Уведомление в Telegram (chat_id 237616472) при СТОП/блокере.
- Откат тривиален: прод `84f1823` не тронут (работаем в worktree).
