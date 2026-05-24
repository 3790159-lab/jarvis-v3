---
category: "issues"
created: "2026-05-24"
tags: ["jarvis", "issues", "bug", "face-swap", "telegram", "deferred"]
bug_id: "B-51"
severity: "medium"
priority: "deferred"
status: "open"
related: ["B-50"]
---
# B-51 — Telegram media_group buffer does not dedupe before handler

## Links
- [[Jarvis-Day4-Handoff]]
- Related: B-50 (defensive dedupe in `BatchOrchestrator.submit_targets`, shipped)

## Summary
Transport-layer (deeper) fix for the duplicate-target problem. The bot wiring
buffers a Telegram `media_group` (album) and flushes it to the batch handler
**without deduplicating**. Network jitter / retry in Telegram update delivery
can cause the same `file_id` to arrive 2–3× per album, so the flushed buffer
carries duplicate path entries.

## Root cause
Bot wiring media_group buffer не дедуплицирует перед передачей в handler.
When Telegram redelivers album updates (retry/race), each redelivered photo is
appended to the in-memory buffer, inflating the entry count beyond the number
of unique files the user actually sent.

## Evidence
- Day 4 hash forensics: **5 incoming files → 10 staged targets**.
- Replication pattern observed: **2 + 2 + 3 + 2 + 1** (variable repeat count per
  unique file, summing to 10 entries over 5 unique contents).
- Result: user saw duplicate outputs; duplicate workflow runs executed on GPU.

## Suspected location
`tools/jarvis_smart_telegram_control.py` — the media_group **buffer flush**
path that feeds `consume_targets_album` (the album buffer is accumulated across
Telegram updates and handed to the orchestrator without a dedupe step).

## Cross-reference to B-50
B-50 added a **defensive** path-level dedupe inside
`BatchOrchestrator.submit_targets`
(`target_paths = list(dict.fromkeys(target_paths))`) — shipped and tested
(regression test `test_submit_targets_dedupes_duplicate_paths`). That closes the
visible UX symptom at the orchestrator boundary. **B-51 is the deeper
transport-layer fix** at the bot wiring buffer, where the duplication actually
originates. The two are complementary: B-50 is the safety net; B-51 prevents the
duplicates from being created in the first place.

## Severity — Medium
UX impact is currently **masked by the B-50 defensive layer** (no more duplicate
outputs reach the user). However, the duplicates still exist upstream of the
orchestrator and, depending on call ordering, can still **waste GPU on duplicate
workflow runs**. Cost/efficiency concern, not a correctness blocker for the user.

## Priority — Deferred
Not blocking (B-50 covers the user-facing symptom). Schedule for **Day 5+**.

## Suggested fix direction (not implemented this session)
Dedupe the album buffer at flush time in the bot wiring (e.g. order-preserving
dedupe on `file_id` / resolved path before calling `consume_targets_album`), so
only unique files are ever submitted. Confirm the exact buffer structure in
`tools/jarvis_smart_telegram_control.py` before implementing.
