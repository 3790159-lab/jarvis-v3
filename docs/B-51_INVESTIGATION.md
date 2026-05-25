# B-51 Investigation — Telegram media_group buffer duplicates targets album

**Status:** Research only (no code changes). Implementation deferred to a
later session pending Daniil's review.
**Date:** 2026-05-25
**Branch / HEAD:** `phase-3.0-inventory-stop-reliability` @ `23b6a30`
**Target file:** `tools/jarvis_smart_telegram_control.py` (5300 lines)

---

## 1. Background & scope

Day-4 hash forensics proved the bot transport layer replicates each Telegram
media_group photo ~2× before the path list reaches
`face_swap_handler.consume_targets_album()`. The engine and orchestrator are
clean — B-50 already shipped a **downstream, path-level** defensive dedupe in
`batch_orchestrator.submit_targets()`:

```python
# app/services/block_m2_face_swap/batch_orchestrator.py:215-218
# B-50 defensive dedupe: bot wiring (Telegram media_group buffer)
# may pass duplicate paths due to retry/race in update delivery.
# Path-level dedupe preserves order and is O(n).
target_paths = list(dict.fromkeys(target_paths))
```

The existing regression test records the **production evidence shape**:

```python
# tests/test_swapbatch_orchestrator.py:146-151
# Simulate buffer with duplicates (2+2+3+2+1 pattern from prod evidence)
sess, _ = orch.submit_targets(42, [img_a, img_a, img_b, img_b, img_a])
# Only 2 unique paths → only 2 staged targets
assert len(sess.targets) == 2
```

The `2+2+3+2+1` multiplicities are **irregular** (not a uniform 2×). That
pattern is a fingerprint of *partial re-processing on retry*, not a single
clean double-delivery — see §3.3.

This document locates the root cause in the transport layer and proposes a
fix at the buffer flush, upstream of the B-50 net.

---

## 2. Entry-point map (which buffer is the culprit)

The bot has **two mutually-exclusive** ingestion modes, selected in
`_main_inner()` by the `WEBHOOK_URL` env var:

| Mode | Trigger | Loop | Album buffering | Flushes? | Reaches `consume_targets_album`? |
|------|---------|------|-----------------|----------|----------------------------------|
| **Polling** (default) | `WEBHOOK_URL` unset | `_main_inner` `while True` @ **5181** | persistent `media_group_buffer` @ **5179** | yes, 2.0 s staleness @ **5184-5199** | **yes** via `_swapbatch_album_intercept` |
| **Webhook** | `WEBHOOK_URL` set | `webhook_reader_thread` → `process_update` @ **5069 / 5007** | **fresh `{}` per call** @ **5009-5010** | **never** | no (album route absent) |

```python
# tools/jarvis_smart_telegram_control.py:5116-5145 (_main_inner)
webhook_url = os.getenv("WEBHOOK_URL", "").strip()
...
if webhook_url:
    ...
    t = _thr.Thread(target=webhook_reader_thread, daemon=True)
    t.start()
    while True:
        time.sleep(60)
    return                # ← polling loop at 5181 is never reached
```

**Conclusion:** the duplication observed at `consume_targets_album` originates
in the **polling-mode buffer** (`_main_inner`), because that is the only path
that routes a buffered album to swapbatch. The webhook `process_update` path
is a separate, currently-dead album path (it appends into a throwaway dict
that nothing ever flushes — see §6, latent bug, out of scope for B-51).

---

## 3. Current logic flow — polling mode (no dedupe)

### 3.1 Buffer accumulation point — `tools/jarvis_smart_telegram_control.py:5285-5291`

```python
elif has_file and str(chat_id) == ALLOWED_CHAT_ID:
    if media_gid:
        # Buffer media group — process when all parts arrive
        if media_gid not in media_group_buffer:
            media_group_buffer[media_gid] = {"msgs": [], "last_seen": time.time()}
        media_group_buffer[media_gid]["msgs"].append(msg)   # ← 5290: append, NO dedupe
        media_group_buffer[media_gid]["last_seen"] = time.time()
    else:
        state = load_state()
        _handle_file_message(chat_id, msg, state)
```

- `media_gid = msg.get("media_group_id")` (line **5238**) is the buffer key.
- Each photo update appends its **whole `msg` dict** to `["msgs"]`.
- There is **no check** for whether an equivalent photo (same
  `file_unique_id`) is already buffered for this group.

### 3.2 Flush trigger — `tools/jarvis_smart_telegram_control.py:5184-5199`

```python
now = time.time()
for gid in list(media_group_buffer):
    buf = media_group_buffer[gid]
    if now - buf["last_seen"] >= 2.0:                 # ← 5187: staleness threshold
        msgs = buf["msgs"]
        if msgs:
            chat_id = str(msgs[0].get("chat", {}).get("id", ""))
            if chat_id == ALLOWED_CHAT_ID:
                if not _swapbatch_album_intercept(chat_id, msgs):   # ← 5193: album route
                    state = load_state()
                    caption = next((m.get("caption", "") for m in msgs if m.get("caption")), "")
                    send(chat_id, f"📦 Получено {len(msgs)} файлов...")
                    for m in msgs:
                        _handle_file_message(chat_id, m, state)
        del media_group_buffer[gid]                   # ← 5199: only deleted AFTER processing
```

- Flush runs at the **top of the poll loop**, before `getUpdates`. A group is
  flushed once it has been idle ≥ 2.0 s.
- `del media_group_buffer[gid]` happens **only after** `_swapbatch_album_intercept`
  / `_handle_file_message` return normally.

### 3.3 Album → targets conversion — `tools/jarvis_smart_telegram_control.py:755-790`

```python
def _swapbatch_album_intercept(chat_id: str, msgs: list) -> bool:
    ...
    paths: list = []
    for m in msgs:                                    # ← one path per buffered msg
        photos = m.get("photo")
        if not photos:
            continue
        largest = sorted(photos, key=lambda p: p.get("file_size", 0))[-1]
        file_id = largest.get("file_id")              # ← 775: keys on file_id (volatile)
        if not file_id:
            continue
        local = _download_telegram_file(
            file_id, f"swapbatch_{int(time.time())}_{file_id[:8]}.jpg",
        )
        if local:
            paths.append(_Path(local))
    ...
    _swapbatch_apply_reply(
        chat_id, handler.consume_targets_album(chat_id_int, paths)   # ← 788
    )
    return True
```

`len(paths) == len(msgs)` — so **duplicate paths ⟺ duplicate `msgs` in the
buffer**. The dedupe must therefore happen on the buffered messages, before
or during this conversion.

### 3.4 How duplicates enter the buffer (mechanism analysis)

The buffer has **zero dedupe**, so any of the following injects duplicate
`msg` entries for the same physical photo:

1. **Re-flush after a mid-flush exception (most consistent with the irregular
   `2+2+3+2+1` shape).** The entire poll-loop body — flush *and* `getUpdates`
   *and* the update `for` loop — is wrapped in one `try/except` that sleeps 3 s
   and retries:

   ```python
   # tools/jarvis_smart_telegram_control.py:5295-5297
   except Exception as e:
       print("ERR:", repr(e), flush=True)
       time.sleep(3)
   ```

   If `_swapbatch_album_intercept` (e.g. a `_download_telegram_file` network
   hiccup on photo *k* of *n*) raises, control jumps to this handler **before**
   `del media_group_buffer[gid]` (5199). The partially-processed group stays
   in the buffer and is **re-flushed in full** on the next iteration —
   re-downloading and re-submitting the photos that already succeeded. Whether
   each photo is duplicated depends on where the failure boundary fell, which
   produces exactly the kind of non-uniform multiplicities recorded in prod.

2. **Telegram update redelivery across a restart.** `offset` is an in-memory
   local initialised to `0` each `_main_inner()` (line **5147**). After a crash
   / restart, `getUpdates` re-fetches not-yet-acked recent updates, re-appending
   their photos.

3. **`file_id` volatility.** Even the downstream filename keys on
   `file_id[:8]` (line **780**), and `file_id` is **not stable** across
   getFile requests / sessions for the same media. Two deliveries of the same
   photo can carry different `file_id`s → different staged filenames →
   different `Path`s, which the B-50 path-level dedupe **cannot** collapse.
   Only `file_unique_id` is guaranteed stable for the same physical media.

The common denominator: nothing keys on a stable per-media identifier. §4
fixes that at the flush.

---

## 4. Telegram update fields (what's available vs what's used)

For a media_group, Telegram delivers **one `message` update per item**, all
sharing the same `media_group_id`. Each message's `photo` field is an array of
`PhotoSize` objects, each carrying both `file_id` and `file_unique_id`.

| Field | Telegram location | Stable? | Used in code today? |
|-------|-------------------|---------|---------------------|
| `media_group_id` | `message.media_group_id` | n/a (group key) | **yes** — buffer key (5238, 5286-5288) |
| `file_id` | `message.photo[i].file_id` | **no** (per-request/bot token) | **yes** — download + filename (734, 775, 780) |
| `file_unique_id` | `message.photo[i].file_unique_id` | **yes** (stable per media, per Telegram API) | **no — never read** (0 occurrences in the file) |

`file_unique_id` is the field the fix should key on. The largest `PhotoSize`
per message gives a consistent per-photo `file_unique_id` (two deliveries of
the same photo select the same largest size → same `file_unique_id`).

---

## 5. Proposed fix design (for review — not yet implemented)

**Principle:** dedupe by stable `file_unique_id` at the moment of buffer
accumulation/flush, in the polling loop — upstream of path generation and the
B-50 net. Keep B-50 as defence-in-depth.

### 5.1 Primary fix — dedupe on append (preferred)

At the accumulation point (5285-5291), track which `file_unique_id`s are
already buffered for each `media_group_id` and skip re-appends:

```python
# sketch — NOT final code
if media_gid:
    buf = media_group_buffer.setdefault(
        media_gid, {"msgs": [], "seen_uids": set(), "last_seen": time.time()}
    )
    uid = _largest_photo_unique_id(msg)   # largest PhotoSize's file_unique_id
    if uid and uid in buf["seen_uids"]:
        pass                              # duplicate delivery — drop silently
    else:
        if uid:
            buf["seen_uids"].add(uid)
        buf["msgs"].append(msg)
    buf["last_seen"] = time.time()
```

Helper (new, pure, trivially testable):

```python
def _largest_photo_unique_id(msg: dict) -> str | None:
    photos = msg.get("photo") or []
    if not photos:
        return None
    largest = sorted(photos, key=lambda p: p.get("file_size", 0))[-1]
    return largest.get("file_unique_id")
```

Dropping on append is robust to **all three** mechanisms in §3.4: a re-flush,
a redelivery, or a `file_id`-variant redelivery all carry the same
`file_unique_id`.

### 5.2 Secondary hardening — make the flush re-entry-safe

To close mechanism §3.4(1) directly, **pop the group out of the buffer before
processing** so an exception cannot trigger a re-flush of the same group:

```python
# sketch
msgs = media_group_buffer.pop(gid)["msgs"]   # remove first, then process
... process msgs ...                          # exception → group already gone
```

This is complementary; even with §5.1, popping-before-processing removes the
"partial re-download" hazard and the duplicate user-facing "📦 Получено N
файлов" message on retry.

### 5.3 Defence-in-depth note

`_swapbatch_album_intercept` (§3.3) could *also* dedupe `file_unique_id`
inline as a belt-and-braces guard, but the buffer-level fix (§5.1) is the
single authoritative point and is what the regression tests should target.

### 5.4 Out of scope for B-51

- The webhook-mode `process_update` throwaway-buffer bug (§6) — separate
  ticket; webhook album handling is currently non-functional regardless.
- B-50's orchestrator path-dedupe stays as-is (defence-in-depth).

---

## 6. Latent secondary bug (noted, not in B-51 scope)

`process_update()` (5007) takes `media_group_buffer=None` and creates a fresh
`{}` when called without one (5009-5010). `webhook_reader_thread` calls it with
**no buffer argument** (5090), so every webhook update gets an independent
empty buffer, and **no code path ever flushes it**. In webhook mode, album
photos are buffered into throwaway dicts and silently dropped; albums never
reach `_swapbatch_album_intercept` at all. This is orthogonal to the polling
duplication but should be filed separately.

---

## 7. Regression test scaffolding plan

Two complementary layers. Both can run fully offline (no Telegram, no network).

### 7.1 Unit test for the dedupe helper (fast, pure)

New file `tests/test_b51_media_group_dedupe.py`. Load the bot module with the
same importlib pattern already used by the webhook tests:

```python
# pattern from tests/test_webhook_mode.py:18-26
def _get_mod():
    spec = importlib.util.spec_from_file_location(
        f"_test_b51_{id(object())}",
        ROOT / "tools" / "jarvis_smart_telegram_control.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod
```

Cases for `_largest_photo_unique_id` (the new helper):
- picks the largest `PhotoSize` and returns its `file_unique_id`;
- returns `None` for a message with no `photo`.

### 7.2 Buffer-flush integration test (the core B-51 guard)

Drive the accumulation + flush path with synthetic updates and assert the
path list handed to `consume_targets_album` is deduped. Mock the boundaries:
- `_download_telegram_file` → return a deterministic path per `file_id`
  (so duplicate file_ids/unique_ids collapse, distinct ones don't);
- `_swapbatch_get_handler` → a fake handler/orch where `is_waiting_for_targets`
  is `True` and `consume_targets_album` records the `len(paths)` it received;
- `send` / `_swapbatch_apply_reply` → no-ops.

Reproduce the prod evidence: feed messages for the **same** `media_group_id`
where the same `file_unique_id` arrives multiple times (e.g. the
`2+2+3+2+1` multiplicity over 2 unique photos), invoke
`_swapbatch_album_intercept(chat_id, msgs)`, and assert
`consume_targets_album` received exactly **2** unique paths.

Add a redelivery variant: the **same** `file_unique_id` carried under **two
different `file_id`s** must still collapse to one path (proves the fix keys on
`file_unique_id`, not `file_id` — the case B-50's path-dedupe can miss).

### 7.3 Flush re-entry test (covers §5.2, if that hardening is adopted)

Make `_download_telegram_file` raise on the first call for one photo; assert
the group is removed from the buffer (popped) and is **not** re-flushed /
re-submitted on the subsequent loop iteration, and that no duplicate "📦
Получено N файлов" message is emitted.

### 7.4 Keep the existing downstream guard green

`tests/test_swapbatch_orchestrator.py::test_submit_targets_dedupes_duplicate_paths`
(B-50) must continue to pass unchanged — the transport fix is additive, not a
replacement.

---

## 8. File/line reference index

| What | File:line |
|------|-----------|
| Poll-loop buffer init | `tools/jarvis_smart_telegram_control.py:5179` |
| Poll-loop flush trigger (2.0 s) | `tools/jarvis_smart_telegram_control.py:5184-5199` |
| Poll-loop buffer accumulation (no dedupe) | `tools/jarvis_smart_telegram_control.py:5285-5291` |
| Poll-loop retry/except (re-flush hazard) | `tools/jarvis_smart_telegram_control.py:5295-5297` |
| `media_group_id` read | `tools/jarvis_smart_telegram_control.py:5238` |
| `_swapbatch_album_intercept` (msgs→paths→consume) | `tools/jarvis_smart_telegram_control.py:755-790` |
| `_swapbatch_photo_intercept` (single photo) | `tools/jarvis_smart_telegram_control.py:714-752` |
| `file_id` usage (download/filename) | `tools/jarvis_smart_telegram_control.py:734, 775, 780` |
| `process_update` (webhook) + throwaway buffer | `tools/jarvis_smart_telegram_control.py:5007-5066` |
| `webhook_reader_thread` calls `process_update` | `tools/jarvis_smart_telegram_control.py:5090` |
| Mode branch (webhook vs poll) | `tools/jarvis_smart_telegram_control.py:5116-5147` |
| B-50 downstream path dedupe | `app/services/block_m2_face_swap/batch_orchestrator.py:215-218` |
| `consume_targets_album` | `app/handlers/face_swap_handler.py:183-200` |
| Prod-evidence regression test | `tests/test_swapbatch_orchestrator.py:135-151` |
| Webhook test harness (importlib pattern) | `tests/test_webhook_mode.py:18-26` |
