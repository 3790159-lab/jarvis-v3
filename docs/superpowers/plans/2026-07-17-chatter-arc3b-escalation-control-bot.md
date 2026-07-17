# Chatter Arc 3B — Escalation + Control Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:test-driven-development for every task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** The funnel owner manages the persona (Аня) from their OWN phone, from their OWN account, by TAPPING inline buttons on cards delivered by a dedicated control bot — never switching accounts, never typing commands. Hot leads escalate to the owner with a 3-second-decision card. Saved Messages stays as a zero-config fallback.

**Architecture:** A `Notifier` interface abstracts "tell the owner something happened, offer these actions." Two implementations: `SavedMessagesNotifier` (arc3a behaviour, text cards + copy-paste hints, Telethon userbot → `me`, NO buttons because userbots can't attach inline keyboards) and `ControlBotNotifier` (a SEPARATE BotFather bot with its OWN token → real inline buttons + callbacks, no 409 with the main Jarvis bot). A small isolated long-poll loop (`control_bot.py`) receives button taps, routes them through a PURE `route_callback`, mutates the shared `Store`, and edits the card for instant feedback. Escalation is decided in TWO layers: free deterministic triggers (playbook keywords, bot-question, guardrail flag) that work even if the network/classifier is down, plus one cheap LLM classifier call returning `{escalate, reason, stage_signal}` that also drives the previously-dead `next_state` funnel. The classifier is fail-safe (DEV-18): garbage/failure → do NOT escalate, log, count; > N failures/hour → alert the owner "classifier degraded."

**Tech Stack:** Python 3.14, Telethon 1.44 (userbot), Telegram Bot API over httpx (control bot), SQLite (`chatter/storage/db.py`), Anthropic Haiku (classifier), pytest.

**THE SEAM (§8):** The 5 core files — `chatter/core/{brain,humanizer,conversation,disclosure,guardrails}.py` — get ZERO edits. We CALL `conversation.next_state`, `disclosure.is_bot_question`, `guardrails.contains_unbacked_claim`, but never edit them. Every commit that could touch the seam is followed by `git diff --stat <core-files>` proving 0 changes; the final report includes the full diff-stat. `run.py` and `telethon_run.py` are orchestrators (not core) and ARE edited — reported explicitly.

**Fallback invariant:** With `control_bot_token_env` unset OR the token missing from the environment, arc3B behaves EXACTLY like arc3a (Saved Messages text cards). Nothing that arc3a proved is allowed to break.

---

## File Structure

**New files:**
- `chatter/core/escalation.py` — PURE deterministic escalation decision + playbook keyword parsing. No network, no LLM, no Telethon.
- `chatter/core/classifier.py` — the cheap LLM classifier (LLM injected). Robust JSON parse, fail-safe. Pure except the injected `LLMClient`.
- `chatter/notify/__init__.py`
- `chatter/notify/base.py` — `Notifier` ABC, `Card`, `Button`, `CardHandle`, `FakeNotifier`.
- `chatter/notify/saved_messages.py` — `SavedMessagesNotifier` (Telethon → `me`, text card, loop-marshaled).
- `chatter/notify/control_bot.py` — `ControlBotNotifier` (Bot API, sync httpx) + `route_callback` (pure) + `ControlBotPoller` (async long-poll IO shell).
- `tests/chatter/test_escalation.py`, `test_classifier.py`, `test_notify_base.py`, `test_notify_saved_messages.py`, `test_control_bot_callbacks.py`, `test_control_bot_poller.py`, `test_escalation_wiring.py`, `test_funnel_next_state.py`.

**Modified (non-core, reported):**
- `chatter/config/loader.py` — extend `ControlConfig` (token env NAME, owner_chat_id, classifier flags, snooze). New `EscalationConfig`? No — keywords come from playbook. Add fields to `ControlConfig`.
- `chatter/core/console.py` — extend `CONSOLE_STRINGS` (escalation card + button labels, ru/en/uk) + `format_escalation_card` + button-label helpers.
- `chatter/run.py` — `Deps` gains optional `notifier` + `classifier` + escalation config; `process_batch` runs the escalation decision + funnel `next_state` on the brain path.
- `chatter/telethon_run.py` — build the notifier (control bot if configured, else Saved Messages), route `post_pause_card` + escalation through it, start the `ControlBotPoller` background task alongside heartbeat/autoresume.
- `chatter/clients/demo/playbook.md`, `demo2/playbook.md` — add a machine-readable escalation-keywords section.
- `chatter/clients/demo/settings.yaml`, `demo2/settings.yaml` — add `control.control_bot_token_env` / `owner_chat_id` (commented/optional; fallback proven with them ABSENT).

---

## Phase A — Config + playbook keywords

### Task A1: Extend `ControlConfig`
**Files:** Modify `chatter/config/loader.py`; Test `tests/chatter/test_loader.py`.

Add to `ControlConfig`:
```python
control_bot_token_env: str | None = None   # NAME of env var holding the bot token, never the value
owner_chat_id: int | None = None            # personal chat of the owner; None = trust-on-first-/start
classifier_enabled: bool = True
classifier_error_threshold: int = 5         # >this many classifier errors within status_window → degraded alert
snooze_seconds: float = 3600.0              # "⏸ Ещё 1ч"
```
Loader reads them from the optional `control:` block with the SAME "defaults from the dataclass instance" pattern already there. `owner_chat_id`: `int(...)` if present else None. `control_bot_token_env`: `str(...)` if present else None.

- [ ] Test: a `control:` block with these keys parses into the fields; absent block → all defaults; `owner_chat_id` coerces to int.
- [ ] Implement, run, commit.

### Task A2: Parse escalation keywords from playbook.md
**Files:** Create `chatter/core/escalation.py`; Modify `chatter/clients/{demo,demo2}/playbook.md`; Test `tests/chatter/test_escalation.py`.

Playbook convention — add a fenced, machine-readable section:
```markdown
## Ключевые слова эскалации
<!-- одно слово/фраза на строку, начинается с "- " -->
- позови
- оплата
- верните
- жалоба
```
`parse_escalation_keywords(playbook: str) -> list[str]`: find the `## Ключевые слова эскалации` heading (also accept EN `## Escalation keywords`, UK `## Ключові слова ескалації`), collect `- ` list items until the next heading, casefold, strip. Missing section → `[]` (deterministic keyword layer simply off — NOT an error).

- [ ] Test: parses the 4 demo keywords; missing section → `[]`; stops at next `##`; casefolds.
- [ ] Implement, run, commit.

---

## Phase B — Escalation decision (deterministic) + classifier + fail-safe

### Task B1: Deterministic escalation
**Files:** `chatter/core/escalation.py`; Test `test_escalation.py`.

```python
@dataclass(frozen=True)
class EscalationReason:
    tag: str        # "keyword" | "bot_question" | "unbacked_claim" | "classifier"
    detail: str     # human one-liner for the card "why"

def deterministic_escalation(
    *, incoming_text: str, reply: str, knowledge: str, keywords: list[str],
) -> EscalationReason | None:
    # 1. keyword hit in incoming_text (casefold substring) -> ("keyword", matched word)
    # 2. is_bot_question(incoming_text) -> ("bot_question", ...)
    # 3. contains_unbacked_claim(reply, knowledge) -> ("unbacked_claim", ...)
    # returns the FIRST hit, else None. CALLS disclosure/guardrails, does not edit them.
```
This is free, no network, works even if the classifier is down (§4 layer 1).

- [ ] Tests: each of the 3 triggers fires with the right tag; clean text+reply → None; keyword match is casefold; empty keywords list disables layer 1.
- [ ] Implement, run, commit. Then `git diff --stat chatter/core/{disclosure,guardrails}.py` → expect empty.

### Task B2: Classifier (cheap LLM call) + fail-safe parse
**Files:** Create `chatter/core/classifier.py`; Test `test_classifier.py`.

```python
STAGE_SIGNALS = {"engaged","interested","needs_human","unknown_info","bought","ghosted"}

@dataclass(frozen=True)
class ClassifierResult:
    escalate: bool
    reason: str        # one-line summary of what the lead wants / why
    stage_signal: str | None   # drives next_state; None if not one of STAGE_SIGNALS
    degraded: bool = False      # True = the call/parse failed; caller must NOT escalate on this

def build_classifier_messages(history: list[dict]) -> list[dict]: ...
def classifier_system_prompt(cfg) -> str:
    # cfg.playbook drives it (§4: "из playbook.md, не хардкод"). Ask ONLY for compact JSON:
    #   {"escalate": bool, "reason": "<=120 chars", "stage_signal": "<one of STAGE_SIGNALS or null>"}

def parse_classifier_reply(raw: str) -> ClassifierResult:
    # tolerant: strip ```json fences, find first {...}, json.loads. On ANY failure ->
    # ClassifierResult(escalate=False, reason="", stage_signal=None, degraded=True). Never raises.
    # stage_signal not in STAGE_SIGNALS -> coerced to None (but escalate/ reason still honored).

def classify(llm, cfg, history) -> ClassifierResult:
    # try: raw = llm.complete(system, msgs, max_tokens=200); return parse_classifier_reply(raw)
    # except Exception: log.exception; return degraded result. Never raises. (§6 DEV-18)
```

- [ ] Tests: valid JSON → parsed; fenced JSON → parsed; garbage/empty/`llm raises` → `degraded=True, escalate=False`; out-of-vocab stage_signal → None; `escalate:true` honored; `reason` truncation tolerated (accept as-is).
- [ ] Implement, run, commit.

### Task B3: Degradation counter + owner alert decision (pure)
**Files:** `chatter/core/classifier.py` (add) OR `escalation.py`; Test `test_classifier.py`.

```python
def note_classifier_error(store, *, now): store.add_event("classifier_error", ts=now)
def classifier_degraded(store, *, now, window_seconds, threshold) -> bool:
    return store.count_events("classifier_error", since_ts=now-window_seconds) > threshold
```
The wiring (Task G) records an error on every `degraded` result, checks `classifier_degraded`, and — debounced via a runtime flag `classifier_degraded_alerted_ts` — alerts the owner ONCE per window (§6: not swallow, not storm).

- [ ] Tests: below threshold → False; above → True; window excludes old errors.
- [ ] Implement, run, commit.

---

## Phase C — Funnel next_state wiring (pure decision, called from orchestrator)

### Task C1: `advance_funnel` helper
**Files:** `chatter/core/escalation.py`; Test `test_funnel_next_state.py`.

```python
def advance_funnel(store, contact_id, *, stage_signal, escalated) -> str:
    """Drive the previously-dead conversation.next_state (§5). Reads current state,
    computes next via next_state(current, signal). If `escalated`, force the 'needs_human'
    signal (so an escalated lead always lands in 'escalated' regardless of classifier signal).
    Persists via store.set_state ONLY if changed. Returns the new state. CALLS
    conversation.next_state; does NOT edit conversation.py."""
```

- [ ] Tests: new+engaged→qualifying persisted; escalated forces needs_human→escalated; None signal → unchanged, no write; terminal state unchanged.
- [ ] Implement, run, commit. Then `git diff --stat chatter/core/conversation.py` → empty.

---

## Phase D — Notifier interface + FakeNotifier + SavedMessages fallback

### Task D1: Notifier ABC + models + FakeNotifier
**Files:** Create `chatter/notify/__init__.py`, `chatter/notify/base.py`; Test `test_notify_base.py`.

```python
class Action(str, Enum):
    RESUME="resume"; SNOOZE="snooze"; OPEN="open"; STOP="stop"; KEEP="keep"

@dataclass(frozen=True)
class Button:
    action: Action
    label: str          # already-localized display label

@dataclass(frozen=True)
class Card:
    kind: str                    # "pause" | "escalation"
    contact_id: str
    text_html: str               # fully rendered, escaped body (buttons rendered separately by impl)
    buttons: list[Button]
    reply_hints: list[str]       # copy-paste fallback lines (Saved Messages uses these; control bot ignores)
    link: str | None = None      # t.me/... for the OPEN button

@dataclass(frozen=True)
class CardHandle:
    ref: str                     # opaque: "me:<msg_id>" or "bot:<chat_id>:<msg_id>"

class Notifier(ABC):
    @abstractmethod
    def notify(self, card: Card) -> CardHandle | None: ...   # sync-facing; None = delivery failed
    @abstractmethod
    def edit(self, handle: CardHandle, text_html: str) -> None: ...   # instant tap feedback
    @property
    @abstractmethod
    def has_buttons(self) -> bool: ...   # True only for control bot

class FakeNotifier(Notifier):
    # records .cards and .edits; has_buttons configurable; returns deterministic handles.
```
Notifier is **sync-facing** (like `Transport`): worker-thread callers use it directly; loop callers wrap with `asyncio.to_thread`.

- [ ] Tests: FakeNotifier records notify/edit; returns a handle; has_buttons togglable.
- [ ] Implement, run, commit.

### Task D2: SavedMessagesNotifier (fallback)
**Files:** Create `chatter/notify/saved_messages.py`; Test `test_notify_saved_messages.py`.

Wraps a Telethon `client` + `loop`. `notify` marshals `client.send_message("me", body, parse_mode="html")` onto the loop via `run_coroutine_threadsafe(...).result(timeout=30)` — the SAME pattern as `send_alert` (safe from a worker thread / `to_thread`; MUST NOT be called directly on the loop). Body = `card.text_html` + `\n\n` + `\n`.join(`card.reply_hints`). `has_buttons=False`. `edit` marshals `client.edit_message`. Returns `CardHandle(ref=f"me:{msg.id}")`. On failure: log, return None (never raises — DEV-18).

- [ ] Tests (mock client with async methods + a real/py loop or a fake marshaler): notify sends to "me" with hints appended, parse_mode html, returns handle; failure → None + logged; has_buttons False.
- [ ] Implement, run, commit.

---

## Phase E — Card rendering (i18n)

### Task E1: Escalation card strings + button labels
**Files:** Modify `chatter/core/console.py`; Test `test_console.py`.

Add to each of ru/en/uk in `CONSOLE_STRINGS`:
```
"btn_resume", "btn_snooze", "btn_open", "btn_stop", "btn_keep"   # button labels with emoji
"esc_header"        # "🔴 Горячий лид: {name}"
"esc_wants"         # "Хочет: {summary}"
"esc_why"           # "Почему: {reason}"
"esc_recent_header" # "Последние сообщения:"
"pause_card_silent" # reuse existing card_* strings where possible
"degraded_alert"    # "⚠️ Классификатор деградировал: {count} ошибок за {hours}ч. Эскалации сейчас только по ключевым словам."
```
Button labels (ru): `▶️ Вернуть Аню`, `⏸ Ещё 1ч`, `💬 Открыть диалог`, `🔴 Стоп везде`, `✅ Оставить Ане`. Feedback strings for edits: `fb_resumed`="▶️ Аня вернулась", `fb_snoozed`="⏸ Пауза ещё на 1ч", `fb_stopped`="🔴 Аня остановлена везде", `fb_kept`="✅ Оставлено Ане", `fb_open`= the link line.

### Task E2: `format_escalation_card` + `pause_buttons`/`escalation_buttons`
**Files:** `chatter/core/console.py`; Test `test_console.py`.

```python
def escalation_buttons(language) -> list[Button]   # resume, snooze, open, stop, keep
def pause_buttons(language) -> list[Button]         # resume, snooze, open, stop  (no keep)
def format_escalation_card(*, name_html, link, summary, reason, recent, language) -> str:
    # recent: list[(role, text)] last 3-5; render each as "<b>кто:</b> snippet" with safe_snippet.
    # Everything user-derived escaped (safe_snippet / display_name already escaped).
```
(`Button` imported from `chatter.notify.base` — note: console.py may import from notify; keep the dependency one-way notify←console? To avoid a cycle, define `Button`/`Action` in `notify.base` and let console.py import them. notify.base must NOT import console. Verify no cycle.)

- [ ] Tests: card contains name, summary, reason, up to 5 recent lines, escaped detail; button label sets correct per language; unknown language → ru.
- [ ] Implement, run, commit. `git diff --stat` core files → empty.

---

## Phase F — Control bot: callbacks (pure) + notifier + poller

### Task F1: `route_callback` (pure)
**Files:** Create `chatter/notify/control_bot.py`; Test `test_control_bot_callbacks.py`.

Callback data format: `f"{action}:{contact_id}"` (contact_id is `"<peer>:<slug>"`; action is an `Action` value). `open` needs a link, carried separately (the card stored its link; but callback can't carry it cleanly → for OPEN we store card→link in `console_cards`? Simpler: OPEN's feedback is just the persisted link; store link at notify time keyed by contact_id, or re-derive t.me from contact — we don't have username in contact_id. DECISION: the escalation/pause card row in `console_cards` already maps msg_id→contact; add link via a new lightweight store method OR encode nothing and have OPEN reply with `tg://` deep-link derivable from peer id: `contact_link(user_id=<peer>)`. Use `contact_link(user_id=peer)` — always works. So OPEN needs no stored link.)

```python
@dataclass(frozen=True)
class CallbackResult:
    feedback_html: str      # new card text after the tap (instant feedback)
    answer: str             # short answerCallbackQuery toast

def route_callback(data: str, *, store, now, language, snooze_seconds) -> CallbackResult:
    # parse "action:contact_id"; dispatch:
    #  resume -> store.unmute + add_event('resume'); feedback fb_resumed
    #  snooze -> store.get_or_create_contact + store.mute(source='command', until=now+snooze); fb_snoozed
    #  stop   -> store.set_runtime_flag('kill_switch','1') + add_event('kill_on'); fb_stopped
    #  keep   -> add_event('escalation_kept'); fb_kept  (no mute change)
    #  open   -> feedback = link line (contact_link(user_id=peer)); no state change
    # unknown action / malformed data -> safe "не понял" toast, NO state change (DEV-18).
```

- [ ] Tests: each action mutates the store correctly + returns right feedback; malformed data → no mutation; unknown action → no mutation; snooze respects snooze_seconds.
- [ ] Implement, run, commit.

### Task F2: ControlBotNotifier (Bot API, sync httpx)
**Files:** `chatter/notify/control_bot.py`; Test `test_control_bot_callbacks.py` (or new).

`ControlBotNotifier(token, chat_id, *, http_post=<injectable>)`. `notify(card)`: POST `sendMessage` with `chat_id`, `text=card.text_html`, `parse_mode=HTML`, `reply_markup={inline_keyboard: [[...]]}` built from `card.buttons` (callback_data=`f"{b.action}:{card.contact_id}"`, one button per row or 2/row). Returns `CardHandle(ref=f"bot:{chat_id}:{message_id}")`. `edit(handle, text)`: POST `editMessageText` (strip buttons on final feedback). `has_buttons=True`. `http_post` injected (default a thin httpx.post wrapper) so tests never hit the network. All failures → log + None/no-op (DEV-18).

- [ ] Tests (inject a fake http_post capturing payloads): notify builds correct inline_keyboard with callback_data per button; edit posts editMessageText; failure → None.
- [ ] Implement, run, commit.

### Task F3: ControlBotPoller (async IO shell)
**Files:** `chatter/notify/control_bot.py`; Test `test_control_bot_poller.py`.

Async long-poll isolated from the Jarvis bot (its OWN token → no 409) and from the persona event handlers:
```python
class ControlBotPoller:
    def __init__(self, token, *, store, language, snooze_seconds, owner_chat_id, notifier,
                 http_get=..., http_post=..., on_bind=None): ...
    async def poll_once(self) -> None:
        # getUpdates(offset, timeout=25); for each update:
        #   message '/start': bind owner (if owner_chat_id None -> trust-on-first-use, persist
        #       runtime_flag 'control_owner_chat_id', call on_bind), reply welcome.
        #   callback_query: gate sender == effective owner_chat_id; else answer "не для вас".
        #       result = route_callback(data, ...); editMessageText(feedback); answerCallbackQuery(answer).
        #   advance offset past the update id.
    async def run_forever(self):  # while True: try poll_once except: log+sleep (never dies) DEV-18
```
`poll_once` is driven in tests with a fake `http_get` returning scripted update batches and a capturing `http_post`. No network, no real bot.

- [ ] Tests: a callback_query from the owner routes + edits + answers + advances offset; a callback from a non-owner is rejected without mutating store; `/start` with no configured owner binds TOFU and persists the flag; getUpdates error in run_forever is swallowed and loop continues.
- [ ] Implement, run, commit.

---

## Phase G — Wiring: escalation into process_batch, notifier + poller into runner

### Task G1: `Deps` gains notifier/classifier/escalation config; `process_batch` runs escalation + funnel
**Files:** Modify `chatter/run.py`; Test `test_escalation_wiring.py`.

`Deps` gains optional: `notifier: Notifier | None = None`, `escalation_keywords: list[str] = []`, `classify: Callable | None = None` (bound classifier), `control: ControlConfig | None = None`. All optional → arc3a behaviour unchanged when absent.

In `process_batch`, on the BRAIN path (not bot-question honest-disclosure short-circuit, not muted), AFTER a reply is decided and BEFORE/independent of sending:
1. `det = deterministic_escalation(incoming_text=text, reply=reply, knowledge=cfg.knowledge, keywords=deps.escalation_keywords)`.
2. If `deps.classify`: `cr = deps.classify(history)`; if `cr.degraded`: `note_classifier_error` + degraded-alert check (via notifier/alert). Else use `cr.escalate/reason/stage_signal`.
3. `escalate = bool(det) or (cr and cr.escalate and not cr.degraded)`.
4. `advance_funnel(store, contact_id, stage_signal=cr.stage_signal if cr else None, escalated=escalate)`.
5. If `escalate` and `deps.notifier`: build the escalation `Card` (name/link need Telethon → the runner supplies a `card_builder` callback; in `run.py` fallback we build a minimal text card from stored history + contact_id) and `deps.notifier.notify(card)`; record the card via `store.add_card(kind="escalation")` when a msg_id is available.

NOTE the existing guardrail block (run.py:163-169) already sets state "escalated" + rewrites the reply. Keep that (it's the honest "I'll check and get back" reply) but now ALSO route it through the notifier via the unified escalation path so the owner gets a card, not just a print. Deduplicate: the guardrail unbacked-claim IS one of the deterministic triggers — unify so we don't escalate twice.

- [ ] Tests (FakeNotifier, FakeLLM, in-memory Store): keyword in incoming → notifier gets an escalation card + state escalated; clean convo → no card; classifier degraded → no escalation from classifier, error counted, deterministic layer still works; funnel advances on stage_signal; notifier absent → no crash (arc3a path).
- [ ] Implement, run, commit. `git diff --stat` core files → empty.

### Task G2: Runner builds notifier, routes pause card through it, starts poller
**Files:** Modify `chatter/telethon_run.py`; Test `test_telethon_run.py`.

- `build_runner`: construct the notifier — if `control.control_bot_token_env` set AND `os.environ[that_name]` present → `ControlBotNotifier(token, owner_chat_id or persisted flag)`; else `SavedMessagesNotifier(client, loop)`. Store on runner; inject into every persona's `Deps` (notifier, escalation_keywords parsed from that persona's playbook, bound classifier, control).
- `post_pause_card`: build a `Card(kind="pause", buttons=pause_buttons(...) if notifier.has_buttons else [], reply_hints=[existing two hint lines])` and deliver via `await asyncio.to_thread(self.notifier.notify, card)`; persist returned handle's msg_id in `console_cards` for Saved-Messages reply addressing (only meaningful for the "me:" ref). Escalation card builder (name/link via `client.get_entity`) supplied to `Deps` as a callback so `process_batch` can enrich the card with a clickable name.
- `_on_connected`: if using the control bot, `loop.create_task(poller.run_forever())` alongside heartbeat/autoresume. Poller shares the same loop but its OWN token.

- [ ] Tests: with token env set, runner builds ControlBotNotifier + schedules poller; without → SavedMessagesNotifier, no poller; pause card goes through notifier; existing arc3a takeover/pause-card tests still green.
- [ ] Implement, run, commit. `git diff --stat` core files → empty.

### Task G3: demo settings + playbook wiring; fallback proven
**Files:** `chatter/clients/{demo,demo2}/settings.yaml`, `playbook.md`.

- Add the escalation-keywords section to both playbooks.
- Add a COMMENTED example `control:` block documenting `control_bot_token_env` / `owner_chat_id` but leave them UNSET so the default test run (and the existing live db) stay on Saved Messages until the owner provides a token. Fallback invariant test: load demo → notifier is SavedMessages.

- [ ] Run full chatter suite green. Commit.

---

## Phase H — Live drill

### Task H1: Pre-drill verification
- [ ] `python -m pytest tests/chatter/ -q` all green.
- [ ] `git diff --stat chatter/core/{brain,humanizer,conversation,disclosure,guardrails}.py` → EMPTY (paste into report).
- [ ] Ask the owner to create the bot in BotFather and set env `CHATTER_CONTROL_BOT_TOKEN` + press /start. Provide the env var NAME.

### Task H2: Drill with the owner
Criterion (§ acceptance): "I managed Аня without ONCE opening the TAMAPI account." Drive a real hot lead → escalation card lands in the control bot on the owner's phone → owner taps ▶️/⏸/💬/🔴/✅ → instant edit feedback → verify state in the userbot. Confirm Saved Messages fallback still works with the token unset. Ping "🔴 Нужен ты" for the drill.

---

## Self-review notes
- Seam: only `next_state`/`is_bot_question`/`contains_unbacked_claim` are CALLED; diff-stat gate after B1, C1, E2, G1, G2.
- Fail-safe (DEV-18): classifier degraded → no escalation + counted + debounced owner alert; poller/notifier never raise out; route_callback ignores malformed data.
- i18n: every new surface string in CONSOLE_STRINGS ru/en/uk; button labels + feedback localized.
- Fallback: token unset → SavedMessagesNotifier, no poller, arc3a behaviour byte-for-byte.
- 409: control bot uses its OWN token, isolated poller — never touches the main Jarvis bot's getUpdates.
