# Chatter — "Configuration without me" (design)

**Status:** implementing (TDD), STOP before merge for owner review.
**Branch:** `chatter-config` (off `phase-4.0-unified-jarvis` @ 5d19d04 = arc3a+3b+3c merged).
**Why:** the product/consulting boundary. If changing a price needs the developer, it's $500 of work,
not $500/mo of product. The client must reconfigure from the pult, on their phone, safely.

## Seam
Five core files (`brain/humanizer/conversation/disclosure/guardrails`) — ZERO edits. Reload logic is a
new `chatter/core/live_config.py` (or runner methods) + config-command formatters in `console.py` +
wiring in the runner/poller. Diff-stat gate after each task.

## 0. The crash-loop mine (must die before the first client)
Today config is validated only at startup: `main() → build_runner → load_personas → load_config`, which
`raise ConfigError` on a bad file → the process exits → the guardian respawns → **crash-loop**. So a
client fat-fingering `settings.yaml` takes Аня down permanently. Two fixes, both required:
- **Reload never crashes** (§2): validate the new config BEFORE swapping; on failure keep the OLD
  in-memory config and report to the pult. A running Аня never dies from a bad edit.
- **Startup fail-safe** (§2b): on start, if the current config is invalid, fall back to the last
  known-good snapshot and alert the owner. Only a first-ever start with no snapshot can hard-fail.

## 1. Config guardrail robustness (owner's question) — VERIFIED, no crash
`guardrails.contains_unbacked_claim` greps numbers from `knowledge.md`. Pressure-tested with empty /
huge-number / emoji-markdown / phone-number / 100k-char / number-soup / binary-ish knowledge: **never
crashes**, always returns a bool. Behavioral nuance only: knowledge with NO numbers → every price in a
reply is "unbacked" → more escalations (conservative, safe). So garbage knowledge cannot break Аня via
the guardrail. No schema needed; free markdown stays a feature.

## 2. Atomic, fail-safe reload  (`reload_persona_configs`)
- Re-read the client dir(s) with `load_config` (the existing validator; its `ConfigError` carries the
  file + reason; YAML errors carry line/column). This happens on a SCRATCH build — nothing live is
  touched yet.
- Rebuild `PersonaBundle`s (new `Config`, new `Brain` with rebuilt system prompt, new escalation
  keywords, new classifier binding) reusing the SAME `Store` (live state — pauses/history/flags — is
  preserved) and re-injecting the runner's `notifier` + `escalation_card`.
- **Success →** atomic swap `runner.personas = new_personas` (single reference assignment; in-flight
  debouncers finish on the old bundle they captured, the NEXT message builds a debouncer off the new
  one — so "Аня names the new price in the next message" holds), then update `runner.allowlist/
  denylist/funnel_gate` from the new primary telegram config, snapshot last-known-good (§2b), stamp
  `config_changed_ts`, return `(True, None)`.
- **Failure →** do NOT swap. Return `(False, human_error)` where `human_error` = the `ConfigError`
  message (file + reason, YAML line when present). Caller reports it to the pult. Аня keeps serving on
  the old config. (DEV-18: not swallowed, surfaced to the owner.)
- Atomicity note: `runner.personas` swap + gate-field assignment are separate; a concurrent
  `handle_event` can at worst read a one-message mix (new gate / old personas). Both halves are
  independently functional; the transient self-heals on the next message. No lock needed.

### 2b. Last-known-good + startup fail-safe
- On every successful load (startup OR reload), copy the client dir's 4 files into
  `clients/<slug>/.versions/<unix_ts>/` (§4). The LATEST version dir IS the last-known-good.
- `build_runner`/startup: try `load_config(clients_dir, slug)`; on `ConfigError`, try the latest
  version dir; on success alert the owner "started on last-known-good, current config is broken:
  <reason>"; only if BOTH fail (first-ever run, no version) does it hard-fail with a clear message.

## 3. Pult commands (work on BOTH surfaces: control bot + Saved Messages)
New global commands, owner-gated: `/config`, `/reload`, `/knowledge [текст]`, `/rollback`.
- These need the RUNNER (rebuild personas / write files), not the pure `execute_command`. New runner
  method `handle_config_command(name, arg, *, reply_document=None) -> str` returns the pult reply text.
- **Control bot** (`ControlBotPoller._on_message`): route `/config|/reload|/knowledge|/rollback` (owner
  only) to `handle_config_command`, reply with the result. This is where the owner lives.
- **Saved Messages** (`_console_handler`): same commands routed to the same runner method (fallback).
- `console.py` gains pure formatters: `format_config(...)`, `format_knowledge(...)` (i18n ru/en/uk).

### /config (human, not a dump)
Persona name + optional age + language; knowledge size (`## ` sections + `- ` bullet counts); gate
on/off (funnel_gate) + allow/deny counts; model; last config change (relative time from
`config_changed_ts`). Optional `persona_age` added to settings (default None → omitted).

### /knowledge
- `/knowledge` (no arg) → show the current `knowledge.md` (truncated safely for Telegram).
- `/knowledge <текст>` → replace `knowledge.md` with the text (version the old first, §4), then reload
  (§2). On reload failure, restore + report (the write itself is followed by a validating reload).
- Forwarded **document** (text/markdown) → download via Bot API getFile, use as the new knowledge.
- **Voice → BACKLOG** (explicit): needs a transcription service (Whisper/API) and audio download;
  text + document already deliver "client updates knowledge without me." Flagged for the owner to
  green-light as a fast-follow. NOT in this arc.

## 4. Versioning + /rollback
- `clients/<slug>/.versions/<unix_ts>/` holds a full copy of the 4 files, written before each change
  and after each good load. Bounded (keep last N, default 20).
- `/rollback` → restore the PREVIOUS version's files over the current ones, then reload (§2). If the
  restored version fails to load (shouldn't — it was good once), keep old + report.
- Client breaks knowledge Friday night → `/rollback` themselves, no phone call.

## 5. mtime auto-reload (optional, from settings)
- `settings.control.auto_reload: bool = False`. When true, a background loop (like heartbeat/autoresume)
  polls the 4 files' mtimes; on change → `reload_persona_configs` (same validate/fail-safe/version).
  Lets the developer edit files by hand and have Аня pick them up live, without a command.
- Off by default (opt-in); a bad hand-edit is fail-safe exactly like /reload.

## 6. Acceptance (live, with owner)
1. Change a price in `knowledge.md` → `/reload` → Аня quotes the NEW price in her next message, no
   restart, no dialog pause.
2. Break `settings.yaml` deliberately → Аня keeps serving on the old config + the pult reports the file
   + reason. Then `/rollback` restores.

## Phases (TDD)
- A: `reload_persona_configs` core (validate→swap|keep-old) + last-known-good snapshot. **critical.**
- B: startup fail-safe (build_runner falls back to last-known-good + alert).
- C: `/config` + `/knowledge` show formatters (pure).
- D: versioning (`snapshot`/`versions`/`restore_previous`) + `/rollback`.
- E: `handle_config_command` runner method + `/knowledge` write + reload.
- F: wire into control-bot poller + Saved Messages console.
- G: mtime auto-reload loop.
- H: forwarded-document knowledge (Bot API getFile). Voice → backlog.
- I: live acceptance.
