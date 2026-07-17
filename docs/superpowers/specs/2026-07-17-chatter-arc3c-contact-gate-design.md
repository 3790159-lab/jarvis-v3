# Chatter Arc 3C — Contact-model admission gate (design)

**Status:** implementing (TDD), STOP before merge for owner review.
**Branch:** `chatter-arc3c` (off `chatter-arc3b`, which is itself awaiting a re-drill before its own merge).

## Problem
The live admission gate is `handle_event`: `if sender_id not in self.allowlist: return`. On a real
funnel account that means Аня answers **only** a hard-coded allowlist — i.e. she answers **no lead**,
because leads are strangers. This is the last functional blocker before the first paying client.

## The flip (owner's requirement)
- **Strangers → answer.** A person NOT in the account's contacts is a lead; Аня engages.
- **Known contacts (Telethon `User.contact == True`) → never answer.** Instead the owner gets a pult
  notification ("your acquaintance X wrote — handle it yourself"). Rationale: a saved contact is a
  friend/colleague/existing relationship, not a funnel lead; auto-replying to them as a lead is wrong
  and embarrassing.
- **Denylist → never answer** (silent block).

## Precondition (MUST be in the spec — owner's requirement)
This gate is only safe on an account **DEDICATED to the funnel**, not the owner's personal account.
On a personal account: every friend who messages becomes a "known contact" → a stream of owner
notifications (noise), and every stranger gets auto-answered (spam/impersonation risk on the owner's
real identity). Therefore the flipped gate is **off by default** and must be explicitly enabled by the
operator setting `funnel_gate: true` in `settings.yaml`, which doubles as the operator's acknowledgement
"this is a dedicated funnel account." With `funnel_gate: false` (default) the old allowlist-only
behaviour is unchanged — nothing that arc2/3A/3B proved breaks.

## Decision table (pure core: `admission_decision`)
Given `sender_id`, `is_contact` (bool), `allowlist`, `denylist`, `funnel_gate` (bool):

| funnel_gate | condition                    | decision          | effect                              |
|-------------|------------------------------|-------------------|-------------------------------------|
| false       | sender in allowlist          | `answer`          | old behaviour (answer only these)   |
| false       | else                         | `ignore`          | old behaviour (silent)              |
| true        | sender in denylist           | `block_denylist`  | silent, logged                      |
| true        | sender in allowlist          | `answer`          | force-answer override (test/VIP)    |
| true        | is_contact (and not above)   | `notify_owner`    | do NOT answer, notify owner in pult |
| true        | else (stranger)              | `answer`          | lead — engage                       |

Precedence (funnel_gate on): denylist > allowlist > contact-check > stranger.
`allowlist` keeps working as a **force-answer override** so the owner can test as a lead even if they
are a saved contact of the funnel account, and to whitelist a known lead who happens to be a contact.

## Owner notification for a known contact
- One notice per contact per `status_window_hours` window (debounced via a runtime flag
  `known_contact_notified:<sender_id>`), so a chatty contact doesn't spam the pult.
- Delivered through the existing `Notifier` (buttonless notice card): "👤 Знакомый {name} написал:
  «{snippet}». Аня не отвечает знакомым. Ответь сам, или /allow {id} чтобы вести его как лида."
- i18n (ru/en/uk).

## Config (`ControlConfig` / `TelegramConfig`)
- `funnel_gate: bool = False` — master switch for the flipped gate (dedicated-account ack).
- `denylist: tuple[int, ...] = ()` — ids never answered.
- `allowlist` — unchanged field, semantics documented above (force-answer override when funnel_gate on).

## Wiring
- `handle_event`: replace the raw allowlist check with `admission_decision(...)`; on `notify_owner`
  send the debounced known-contact notice; on `block_*`/`ignore` return silently.
  `is_contact` = `getattr(event.sender, "contact", False)`.
- `select_missed` (catch-up): same decision — a stranger who wrote while offline is still a lead; a
  contact is still not answered. The dialog snapshot dict gains `is_contact`
  (from `entity.contact` in `_collect_dialogs`).
- `contact_id_for_chat` / outgoing-takeover: unchanged — those key off existing contact rows +
  allowlist, and the owner's own outgoing handling is orthogonal to admission.

## Seam
Five core files (`brain/humanizer/conversation/disclosure/guardrails`) untouched. All logic lives in a
new pure `admission.py` (or extends `chatter/telethon_run.py` helpers) + config + wiring. Diff-stat gate
after each task; report proves 0 core changes.

## Out of scope (backlog)
- `/allow <id>` control-bot command + a runtime allowlist (persisted in SQLite) so the owner can
  convert a known contact into a lead WITHOUT editing settings.yaml + restart. MVP ships only the
  known-contact notice; conversion today = add the id to the settings `allowlist` and restart.
- A pult button "[✅ вести как лида]" on the known-contact notice that does the above on tap.
- Auto-learning denylist.
