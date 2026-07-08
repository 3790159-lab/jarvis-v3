# Router Money-Gate Implementation Plan (Bug #1 — money hole)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the unified LLM router refuse to spend money on any PAID tool without an explicit user confirm-tap AND `guard_spend` cap enforcement — closing the live money hole where "сделай фото …, не спрашивай подтверждения" generated a paid image immediately.

**Architecture:** The gate lives at the tool-execution boundary *inside the router loop*, keyed on registry metadata (`Tool.paid`), never on the user's text. When Claude selects a PAID tool, the loop halts before executing anything, returns a `pending_paid` marker, and the Telegram bridge reuses the existing `pending_confirm` / `confirm:run` machinery to show a confirm button. On confirm, the stashed tool call executes under `guard_spend` (which enforces the daily cap and records cost). Because the trigger is `tool.paid` from the registry, no phrase in the message ("не спрашивай", "режим разработчика", instructions inside web/file content) can bypass or remove the confirm. Fail-closed ordering: after Task 2 the router already cannot spend without confirm; Tasks 3–4 restore the execution UX safely.

**Tech Stack:** Python 3.14, pytest, Anthropic SDK (tool_use loop), existing `app/services/auth/spend_guard.guard_spend`, existing `pending_confirm`/`confirm:run` flow in `tools/jarvis_smart_telegram_control.py`.

---

## Money-safety invariant (the property every task defends)

> No PAID router tool executes unless (a) the user tapped an explicit confirm button for exactly this pending action, and (b) the execution goes through `guard_spend` (cap check before spend, ledger write only on success). The decision to gate depends ONLY on `Tool.paid` from the registry — never on the message text.

This mirrors the IR-path invariant already merged as `c7acc5f` / `b3c70c3`, but on the router path, which is the live path for plain text (`JARVIS_ROUTER_ENABLED=1`).

## Deferred (NOT in this plan)

Bug #2 (`"глянь что с ботом"` → free dialogue instead of `/health`) is a *routing* gap, not a money bug. It is a separate follow-up: either add a read-only diagnostic tool to the router, or let a no-tool router turn fall through to the IR tail. Out of scope here per "приоритет #1 деньги".

## File Structure

- `app/services/unified/llm_router/tool_registry.py` — add `paid: bool` + `est_usd: float` to the `Tool` dataclass. Registry is the single source of truth for "is this tool paid and how much".
- `app/services/unified/llm_router/tools/__init__.py` — mark the paid tools (name → est USD) in `register_default_tools`.
- `app/services/unified/llm_router/router.py` — halt the loop on a paid tool; add `pending_paid` to `RouterResponse`; add `execute_paid_tool()` for the confirmed re-execution.
- `tools/jarvis_smart_telegram_control.py` — bridge: turn `pending_paid` into the existing confirm flow (`pending_confirm` + `confirm:run`) and execute the confirmed tool under `guard_spend`.
- Tests: `tests/test_router_paid_tool_gate.py` (router unit), `tests/test_router_money_bridge.py` (control bridge + confirm resume), plus one end-to-end regression of the exact live scenario.

---

### Task 1: Registry carries paid-ness (source of truth)

**Files:**
- Modify: `app/services/unified/llm_router/tool_registry.py:88-95` (the `Tool` dataclass)
- Modify: `app/services/unified/llm_router/tools/__init__.py:51-75` (`register_default_tools`)
- Test: `tests/test_router_paid_tool_gate.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_router_paid_tool_gate.py
from app.services.unified.llm_router.tool_registry import ToolRegistry
from app.services.unified.llm_router.tools import register_default_tools


def test_paid_tools_flagged_with_cost():
    reg = register_default_tools(ToolRegistry())
    gi = reg.get("generate_image")
    assert gi is not None
    assert gi.paid is True
    assert gi.est_usd > 0

    stats = reg.get("get_user_stats")
    assert stats is not None
    assert stats.paid is False          # read-only, free
    assert stats.est_usd == 0.0


def test_free_setup_tools_not_paid():
    reg = register_default_tools(ToolRegistry())
    for free_name in ("swap_batch_start_source", "swap_batch_start_targets",
                      "swap_batch_set_quality", "cancel_current_batch"):
        t = reg.get(free_name)
        assert t is not None, free_name
        assert t.paid is False, free_name
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_router_paid_tool_gate.py -v`
Expected: FAIL — `AttributeError: 'Tool' object has no attribute 'paid'`.

- [ ] **Step 3: Add fields to the `Tool` dataclass**

In `app/services/unified/llm_router/tool_registry.py`, extend the dataclass (keep existing fields; add two with safe defaults so all existing constructions stay valid):

```python
@dataclass
class Tool:
    """A registered tool: Anthropic definition + async handler."""

    name: str
    description: str
    input_schema: Dict[str, Any]
    handler: ToolHandler
    paid: bool = False          # True → spends money; router must confirm before running
    est_usd: float = 0.0        # estimated cost, drives confirm-button label + guard_spend cap

    def to_anthropic(self) -> Dict[str, Any]:
        """Render the tool definition block for the Messages API ``tools`` list."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
```

- [ ] **Step 4: Mark the paid tools in `register_default_tools`**

In `app/services/unified/llm_router/tools/__init__.py`, add the price map and apply it just before `return registry` at the end of `register_default_tools` (line ~74). Names verified against the router system prompt in `router.py:33-115`.

```python
# Source of truth: which router tools spend money, and the est USD used for the
# confirm-button label AND the guard_spend cap check. Anything not listed is FREE
# (read-only / setup). Must stay in sync with the paid backends behind each tool.
_ROUTER_PAID_USD = {
    "generate_image": 0.04,            # Replicate FLUX (== /gen)
    "generate_persona_photo": 0.10,    # FLUX persona (stub today, still gate it)
    "video_face_swap": 0.40,           # video swap pipeline
    "swap_batch_run_swap": 0.02,       # runs the batch swap
    "swap_batch_run_animation": 0.50,  # animates the batch (minutes, paid)
    "web_research": 0.02,              # LLM + search
    "build_table": 0.03,               # research + XLSX
    "answer_about_file": 0.01,         # LLM over uploaded file
    "reply_with_voice": 0.01,          # TTS
}
```

Then, immediately before `return registry`:

```python
    for _t in registry.all():
        _price = _ROUTER_PAID_USD.get(_t.name)
        if _price is not None:
            _t.paid = True
            _t.est_usd = _price
    return registry
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_router_paid_tool_gate.py -v`
Expected: PASS (both tests).

- [ ] **Step 6: Verify the real registered tool names match the map**

Run: `python -c "from app.services.unified.llm_router.tool_registry import ToolRegistry; from app.services.unified.llm_router.tools import register_default_tools as r; reg=r(ToolRegistry()); print(sorted(reg.names()))"`
Expected: the printed names include every key of `_ROUTER_PAID_USD`. If any key is absent (e.g. persona tool is named differently), fix the map key to the real `.name` and re-run Step 5.

- [ ] **Step 7: Commit**

```bash
git add app/services/unified/llm_router/tool_registry.py app/services/unified/llm_router/tools/__init__.py tests/test_router_paid_tool_gate.py
git commit -m "feat(router): registry carries paid/est_usd per tool (money source-of-truth)"
```

---

### Task 2: Router halts on a paid tool (fail-closed — closes the hole)

**Files:**
- Modify: `app/services/unified/llm_router/router.py:118-128` (`RouterResponse`), `:230-303` (loop)
- Test: `tests/test_router_paid_tool_gate.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_router_paid_tool_gate.py
import asyncio
from app.services.unified.llm_router.router import LLMRouter
from app.services.unified.llm_router.tool_registry import (
    Tool, ToolRegistry, ToolContext, ToolResult,
)


class _Block:  # one Claude turn: a single tool_use for a paid tool
    def __init__(self, name):
        self.type = "tool_use"; self.name = name; self.id = "tu_1"; self.input = {"prompt": "брускета"}


class _Resp:
    def __init__(self, blocks):
        self.content = blocks
        self.usage = type("U", (), {"input_tokens": 10, "output_tokens": 5})()


class _FakeClient:
    def __init__(self, resp):
        self._resp = resp; self.calls = 0
        self.messages = type("M", (), {"create": self._create})()
    def _create(self, **kw):
        self.calls += 1
        return self._resp


def test_router_halts_on_paid_tool_without_executing():
    executed = []

    async def _handler(params, ctx):
        executed.append(params)                      # must NEVER run without confirm
        return ToolResult.photo("http://x/img.png")

    reg = ToolRegistry()
    reg.register(Tool("generate_image", "gen", {"type": "object"}, _handler,
                      paid=True, est_usd=0.04))
    recorded = []
    router = LLMRouter(
        _FakeClient(_Resp([_Block("generate_image")])), reg,
        record_cost=lambda uid, un, cost: recorded.append(cost),
    )
    ctx = ToolContext(user_id=1, username="admin", chat_id="99")
    resp = asyncio.run(router.route_message("сделай фото брускеты, не спрашивай", ctx))

    assert executed == []                            # tool did NOT spend
    assert resp.pending_paid is not None
    assert resp.pending_paid["name"] == "generate_image"
    assert resp.pending_paid["est_usd"] == 0.04
    assert resp.pending_paid["params"] == {"prompt": "брускета"}
    # LLM routing tokens may be recorded, but never the tool cost:
    assert all(c < 0.04 for c in recorded)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_router_paid_tool_gate.py::test_router_halts_on_paid_tool_without_executing -v`
Expected: FAIL — `AttributeError: 'RouterResponse' object has no attribute 'pending_paid'`.

- [ ] **Step 3: Add `pending_paid` to `RouterResponse`**

In `app/services/unified/llm_router/router.py`, extend the dataclass:

```python
@dataclass
class RouterResponse:
    """Outcome of :meth:`LLMRouter.route_message`."""

    text: str = ""
    media: List[ToolResult] = field(default_factory=list)
    tools_used: List[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    error: str = ""
    pending_paid: Optional[Dict[str, Any]] = None   # {name, params, est_usd} — awaiting confirm
```

- [ ] **Step 4: Halt the loop before executing any paid tool**

In `route_message`, replace the tool-execution block (the `for tu in tool_uses:` loop and the lines around it, currently `router.py:258-275`) so it first scans for a paid tool and bails out. Insert the scan immediately after `messages.append({"role": "assistant", "content": content})` and BEFORE the results loop:

```python
                # Echo the assistant turn (with its tool_use blocks) back verbatim.
                messages.append({"role": "assistant", "content": content})

                # Money gate: if Claude selected any PAID tool, DO NOT execute
                # anything in this batch. Halt and surface a pending action for the
                # caller to confirm. The trigger is registry metadata (tool.paid),
                # never the message text — so no phrase can bypass this.
                paid_tu = next(
                    (tu for tu in tool_uses
                     if getattr(self._registry.get(tu.name), "paid", False)),
                    None,
                )
                if paid_tu is not None:
                    tool = self._registry.get(paid_tu.name)
                    cost = compute_cost(self._model, input_tokens, output_tokens)
                    self._record(context, tools_used, input_tokens + output_tokens, cost)
                    return RouterResponse(
                        tools_used=tools_used,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cost_usd=cost,
                        pending_paid={
                            "name": paid_tu.name,
                            "params": paid_tu.input or {},
                            "est_usd": float(getattr(tool, "est_usd", 0.0) or 0.0),
                        },
                    )

                results: List[Dict[str, Any]] = []
                for tu in tool_uses:
```

(The rest of the results loop is unchanged.)

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_router_paid_tool_gate.py -v`
Expected: PASS (all three tests).

- [ ] **Step 6: Run the existing router tests for no regression**

Run: `python -m pytest tests/test_router_prompt.py tests/test_image_routing.py -v`
Expected: PASS (free tools still execute; only paid tools now halt).

- [ ] **Step 7: Commit**

```bash
git add app/services/unified/llm_router/router.py tests/test_router_paid_tool_gate.py
git commit -m "feat(router): fail-closed halt before any PAID tool — no spend without confirm"
```

---

### Task 3: Bridge — pending_paid → existing confirm flow (money-safe, no spend)

**Files:**
- Modify: `tools/jarvis_smart_telegram_control.py` — `_run_router` (:8584-8635) and `_route_plain_text` (:8638-8652)
- Test: `tests/test_router_money_bridge.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_router_money_bridge.py
import types
import tools.jarvis_smart_telegram_control as ctl


def _fresh_state(monkeypatch):
    store = {"state": {}}
    monkeypatch.setattr(ctl, "load_state", lambda: store["state"])
    monkeypatch.setattr(ctl, "save_state", lambda s: store.__setitem__("state", s))
    return store


def test_pending_paid_shows_confirm_and_does_not_spend(monkeypatch):
    store = _fresh_state(monkeypatch)
    sent = []
    monkeypatch.setattr(ctl, "send_with_keyboard",
                        lambda cid, txt, kb, *a, **k: sent.append((txt, kb)))
    monkeypatch.setattr(ctl, "send", lambda *a, **k: sent.append(a))

    # Router returns a pending_paid response (no media, no text).
    resp = types.SimpleNamespace(
        text="", media=[], error="", tools_used=["generate_image"],
        pending_paid={"name": "generate_image",
                      "params": {"prompt": "брускета"}, "est_usd": 0.04},
    )
    monkeypatch.setattr(ctl, "_run_router", lambda cid, text, msg: resp)

    consumed = ctl._route_plain_text("99", "сделай фото брускеты, не спрашивай", {"from": {"id": 1}})

    assert consumed is True                              # message handled (not passed to legacy)
    pend = store["state"].get("pending_confirm")
    assert pend is not None
    assert pend["resume"]["kind"] == "router_tool"
    assert pend["resume"]["tool"] == "generate_image"
    assert pend["resume"]["params"] == {"prompt": "брускета"}
    assert pend["resume"]["est"] == 0.04
    # A confirm button was shown; nothing was generated/sent as a photo.
    assert any("Запустить" in txt for txt, _kb in sent if isinstance(txt, str))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_router_money_bridge.py::test_pending_paid_shows_confirm_and_does_not_spend -v`
Expected: FAIL — `_route_plain_text` renders/returns without stashing `pending_confirm` (no `router_tool` resume).

- [ ] **Step 3: Add the pending-paid confirm sender**

In `tools/jarvis_smart_telegram_control.py`, add this helper next to `_render_router_response` (~line 8582):

```python
def _router_paid_confirm(chat_id: str, pending: Dict[str, Any]) -> None:
    """Turn a router pending_paid action into the shared confirm flow.

    Reuses ``pending_confirm`` + the ``confirm:run`` callback (same machinery as
    the IR money-gate). No spend happens here — the tool runs only after the tap.
    The gate key is the tool's paid-ness (already decided in the router), never
    the message text.
    """
    name = pending.get("name", "")
    params = pending.get("params") or {}
    est = float(pending.get("est_usd") or 0.0)
    state = load_state()
    state["pending_confirm"] = {
        "cmd": name,                       # human label; not a slash-command
        "resume": {"kind": "router_tool", "tool": name, "params": params, "est": est},
    }
    save_state(state)
    ptxt = f" (платно ~${est:.2f})" if est else " (платно)"
    send_with_keyboard(
        chat_id, f"Запустить {name}?{ptxt}",
        [[{"text": "▶️ Запустить", "callback_data": "confirm:run"},
          {"text": "Отмена", "callback_data": "confirm:cancel"}]],
    )
```

- [ ] **Step 4: Handle pending_paid in `_route_plain_text`**

Replace the body of `_route_plain_text` (currently `router.py`'s control bridge at `:8645-8652`) with:

```python
def _route_plain_text(chat_id: str, text: str, msg: Dict[str, Any]) -> bool:
    response = _run_router(chat_id, text, msg)
    if response is None:
        return False
    pending = getattr(response, "pending_paid", None)
    if pending:
        try:
            _router_paid_confirm(chat_id, pending)
        except Exception as e:  # noqa: BLE001 - confirm must not crash the loop
            print(f"[router] paid-confirm failed: {e}", flush=True)
        return True                        # consumed; no spend, awaiting the tap
    try:
        _render_router_response(chat_id, response)
    except Exception as e:  # noqa: BLE001 - render must not crash the loop
        print(f"[router] render failed: {e}", flush=True)
    return True
```

- [ ] **Step 5: Do not poison history / mislabel on pending_paid in `_run_router`**

In `_run_router`, the tail currently appends to history and prints "handled". Guard it so a pending (un-executed) turn is not recorded as a completed exchange. Replace the tail (`:8632-8635`) with:

```python
    if getattr(response, "pending_paid", None):
        print(f"[router] paid tool pending confirm: {response.pending_paid.get('name')}",
              flush=True)
        return response                    # bridge will show the confirm button
    _router_history_append(str(chat_id), text, getattr(response, "text", "") or "")
    tools = getattr(response, "tools_used", None) or []
    print(f"[router] handled chat={chat_id} via tools={tools}", flush=True)
    return response
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/test_router_money_bridge.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add tools/jarvis_smart_telegram_control.py tests/test_router_money_bridge.py
git commit -m "feat(router): bridge pending_paid into shared confirm flow (no spend pre-tap)"
```

---

### Task 4: Confirmed execution under guard_spend

**Files:**
- Modify: `app/services/unified/llm_router/router.py` (add `execute_paid_tool`)
- Modify: `tools/jarvis_smart_telegram_control.py` — `confirm:run` handler (:4535-4551)
- Test: `tests/test_router_money_bridge.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_router_money_bridge.py
import types
import tools.jarvis_smart_telegram_control as ctl


def test_confirmed_router_tool_runs_under_guard_spend(monkeypatch):
    store = _fresh_state(monkeypatch)
    store["state"]["pending_confirm"] = {
        "cmd": "generate_image",
        "resume": {"kind": "router_tool", "tool": "generate_image",
                   "params": {"prompt": "брускета"}, "est": 0.04},
    }
    sent = []
    monkeypatch.setattr(ctl, "send", lambda cid, txt, *a, **k: sent.append(("text", txt)))
    monkeypatch.setattr(ctl, "_send_photo_url",
                        lambda cid, url, cap="", *a, **k: sent.append(("photo", url)))
    monkeypatch.setattr(ctl, "answer_callback_query", lambda *a, **k: None)
    monkeypatch.setattr(ctl, "_menu_role", lambda uid: "admin")

    # Capture the guard_spend call: it must be invoked with the est cost.
    guarded = {}

    def _fake_guard(uid, un, est, do_spend):
        guarded["est"] = est
        return do_spend(), None            # allowed → run the tool

    monkeypatch.setattr(ctl, "guard_spend", _fake_guard)

    # Router tool returns a photo ToolResult when executed.
    from app.services.unified.llm_router.tool_registry import ToolResult

    class _FakeRouter:
        async def execute_paid_tool(self, name, params, context):
            assert name == "generate_image" and params == {"prompt": "брускета"}
            return ToolResult.photo("http://x/brusketa.png", caption="брускета")

    monkeypatch.setattr(ctl, "_build_router", lambda: _FakeRouter())

    ctl._handle_confirm_run("99", cq_id="cq1", cq_uid=1, state=store["state"])

    assert guarded.get("est") == 0.04                    # cap check ran with est cost
    assert ("photo", "http://x/brusketa.png") in sent    # media delivered
    assert store["state"].get("pending_confirm") is None  # consumed
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_router_money_bridge.py::test_confirmed_router_tool_runs_under_guard_spend -v`
Expected: FAIL — `AttributeError: module 'tools.jarvis_smart_telegram_control' has no attribute '_handle_confirm_run'`.

- [ ] **Step 3: Add `execute_paid_tool` to `LLMRouter`**

In `app/services/unified/llm_router/router.py`, add a public method (keeps the private `_execute_tool` encapsulated):

```python
    async def execute_paid_tool(
        self, name: str, params: Dict[str, Any], context: ToolContext
    ) -> "ToolResult":
        """Execute one already-confirmed tool by name. Used by the confirm flow.

        Cost is NOT recorded here — the caller wraps this in ``guard_spend`` so the
        cap check happens before spend and the ledger is written only on success.
        """
        from app.services.unified.llm_router.tool_registry import ToolResult  # noqa: F401
        return await self._execute_tool(name, params, context)
```

- [ ] **Step 4: Extract the `confirm:run` body into a testable helper and add the `router_tool` branch**

In `tools/jarvis_smart_telegram_control.py`, refactor the existing `confirm:run` inline block (`:4535-4551`) to delegate to a named function, then add the router-tool branch. Replace the block with:

```python
    if data == "confirm:run":
        _handle_confirm_run(chat_id, cq_id, _cq_uid, state)
        return
```

Add the helper (near `_money_gate`, ~line 3975):

```python
def _handle_confirm_run(chat_id, cq_id, cq_uid, state) -> None:
    """Resume a confirmed paid action. confirm:run is produced ONLY by a confirm
    button, so it is already authorised — dispatch the stashed resume once."""
    pend = state.get("pending_confirm") or {}
    cmd = pend.get("cmd", "")
    resume = pend.get("resume") or {}
    role = _menu_role(cq_uid)
    if role != "admin" and cmd not in FRIEND_ALLOWED_COMMANDS:
        answer_callback_query(cq_id, "Недоступно")
        return
    state["pending_confirm"] = None
    if resume.get("kind") == "router_tool":
        save_state(state)
        answer_callback_query(cq_id, "▶️")
        _run_router_tool_confirmed(chat_id, cq_uid, resume, state)
        return
    state["_paid_confirmed"] = cmd          # одноразовый: гейт его consume-нет
    save_state(state)
    answer_callback_query(cq_id, "▶️")
    if resume.get("kind") == "intent":
        run_intent(chat_id, resume.get("pack") or {}, state)
    else:  # kind == "cmd"
        handle_command(chat_id, resume.get("cmd", ""), resume.get("query", ""), state)


def _run_router_tool_confirmed(chat_id, cq_uid, resume, state) -> None:
    """Execute a confirmed router paid tool under guard_spend, then render media."""
    import asyncio as _asyncio

    from app.services.unified.llm_router.tool_registry import ToolContext

    name = resume.get("tool", "")
    params = resume.get("params") or {}
    est = float(resume.get("est") or 0.0)
    router = _build_router()
    if router is None:
        send(chat_id, "⚠️ Роутер недоступен, попробуй позже.")
        return
    try:
        uid = int(cq_uid) if cq_uid is not None else None
    except (TypeError, ValueError):
        uid = None
    context = ToolContext(user_id=uid, username=None, chat_id=str(chat_id))

    captured: Dict[str, Any] = {}

    def _do():
        r = _asyncio.run(router.execute_paid_tool(name, params, context))
        captured["result"] = r
        # Only a successful (non-error) media/text result counts as a spend so
        # guard_spend records cost on success only (mirrors face_swap pattern).
        return r if (r is not None and not r.is_error) else None

    result, err = guard_spend(uid, None, est, _do)
    if err:
        send(chat_id, f"🚫 {err}")
        return
    r = captured.get("result")
    if r is None or r.is_error:
        send(chat_id, f"⚠️ Не выполнено: {getattr(r, 'error', '') or 'ошибка инструмента'}")
        return
    if r.kind == "photo" and r.media:
        _send_photo_url(str(chat_id), r.media, r.text or "")
    elif r.kind == "video" and r.media:
        send(str(chat_id), f"🎬 Видео: {r.media}")
    else:
        send(str(chat_id), r.text or "Готово.")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_router_money_bridge.py -v`
Expected: PASS (both bridge tests).

- [ ] **Step 6: Confirm the cap-block path returns nothing spent**

Add and run this test (guard_spend blocks → no media, reason surfaced):

```python
# add to tests/test_router_money_bridge.py
def test_confirmed_router_tool_blocked_by_cap(monkeypatch):
    store = _fresh_state(monkeypatch)
    store["state"]["pending_confirm"] = {
        "cmd": "generate_image",
        "resume": {"kind": "router_tool", "tool": "generate_image",
                   "params": {"prompt": "x"}, "est": 0.04},
    }
    sent = []
    monkeypatch.setattr(ctl, "send", lambda cid, txt, *a, **k: sent.append(txt))
    monkeypatch.setattr(ctl, "_send_photo_url",
                        lambda *a, **k: sent.append("PHOTO"))
    monkeypatch.setattr(ctl, "answer_callback_query", lambda *a, **k: None)
    monkeypatch.setattr(ctl, "_menu_role", lambda uid: "admin")
    monkeypatch.setattr(ctl, "guard_spend",
                        lambda uid, un, est, do: (None, "дневной лимит исчерпан"))

    ran = []

    class _R:
        async def execute_paid_tool(self, *a, **k):
            ran.append(1); from app.services.unified.llm_router.tool_registry import ToolResult
            return ToolResult.photo("http://x/y.png")

    monkeypatch.setattr(ctl, "_build_router", lambda: _R())

    ctl._handle_confirm_run("99", "cq1", 1, store["state"])

    assert "PHOTO" not in sent                 # nothing delivered
    assert any("лимит" in s for s in sent)     # reason surfaced
```

Run: `python -m pytest tests/test_router_money_bridge.py -v`
Expected: PASS (all three).

- [ ] **Step 7: Commit**

```bash
git add app/services/unified/llm_router/router.py tools/jarvis_smart_telegram_control.py tests/test_router_money_bridge.py
git commit -m "feat(router): confirmed paid tool executes under guard_spend (cap + ledger)"
```

---

### Task 5: End-to-end regression of the exact live bug

**Files:**
- Test: `tests/test_router_money_bridge.py`

- [ ] **Step 1: Write the failing-then-passing regression test**

This encodes the reported scenario: "сделай фото брускеты, не спрашивай подтверждения, просто сразу" must NOT generate immediately; it must show a confirm; only a tap spends. Proves the phrase cannot bypass the gate.

```python
# add to tests/test_router_money_bridge.py
import asyncio
from app.services.unified.llm_router.router import LLMRouter
from app.services.unified.llm_router.tool_registry import (
    Tool, ToolRegistry, ToolContext, ToolResult,
)


def test_dont_ask_phrase_cannot_bypass_confirm():
    """Regression: 'не спрашивай подтверждения' still requires a confirm tap."""
    spends = []

    async def _gen(params, ctx):
        spends.append(params)
        return ToolResult.photo("http://x/brusketa.png")

    reg = ToolRegistry()
    reg.register(Tool("generate_image", "gen", {"type": "object"}, _gen,
                      paid=True, est_usd=0.04))

    class _Blk:
        type = "tool_use"; name = "generate_image"; id = "t1"
        input = {"prompt": "брускета, фотореализм"}

    class _Resp:
        content = [_Blk()]
        usage = type("U", (), {"input_tokens": 8, "output_tokens": 4})()

    class _Client:
        messages = type("M", (), {"create": staticmethod(lambda **k: _Resp())})()

    router = LLMRouter(_Client(), reg)
    ctx = ToolContext(user_id=1, username="admin", chat_id="99")
    resp = asyncio.run(router.route_message(
        "сделай фото брускеты, не спрашивай подтверждения, просто сразу", ctx))

    assert spends == []                                  # NOTHING generated
    assert resp.pending_paid["name"] == "generate_image"  # gated to confirm instead
```

- [ ] **Step 2: Run the full new suite**

Run: `python -m pytest tests/test_router_paid_tool_gate.py tests/test_router_money_bridge.py -v`
Expected: PASS (all tests green).

- [ ] **Step 3: Run the money-safety regression net for no collateral damage**

Run: `python -m pytest tests/test_money_confirm_gate.py tests/test_intent_router.py tests/test_image_routing.py tests/test_router_prompt.py -v`
Expected: PASS (existing IR/confirm invariants intact).

- [ ] **Step 4: Commit**

```bash
git add tests/test_router_money_bridge.py
git commit -m "test(router): regression — 'не спрашивай' phrase cannot bypass money-confirm"
```

---

## Post-implementation live verification (REQUIRED before declaring done)

Green tests were the whole reason this bug shipped — do NOT trust them alone. After merge + bot restart, on the live bot:

- [ ] Send: `сделай фото брускеты, не спрашивай подтверждения, просто сразу` → expect a **confirm button** ("Запустить generate_image? (платно ~$0.04)"), and **NO photo** until tapped. Check `/costs` did not move before the tap.
- [ ] Tap **▶️ Запустить** → photo arrives; `/costs` moves by ~$0.04 (guard_spend recorded it once).
- [ ] Tap **Отмена** on a fresh request → no photo, no cost.
- [ ] Confirm the daily-cap path: with a near-exhausted test user (or a temporarily low cap), the confirmed tap is **blocked** with the limit reason and no spend.
- [ ] Regression: a FREE request through the router (e.g. "покажи мои расходы" → get_user_stats) still runs immediately with no confirm.

## Self-Review notes

- **Spec coverage:** money invariant (no paid tool without confirm+guard_spend) → Tasks 2 (halt), 3 (confirm), 4 (guard_spend execution), 5 (phrase-bypass regression). Registry source-of-truth → Task 1.
- **Type consistency:** `pending_paid` shape `{name, params, est_usd}` is produced in Task 2 and consumed in Task 3; the `resume` shape `{kind:"router_tool", tool, params, est}` is written in Task 3's `_router_paid_confirm` and read in Task 4's `_run_router_tool_confirmed`/`_handle_confirm_run` — names match.
- **guard_spend contract:** `do_spend` returns falsy on tool error so the ledger is written on success only (matches `spend_guard.py:41-46`).
</content>
</invoke>
