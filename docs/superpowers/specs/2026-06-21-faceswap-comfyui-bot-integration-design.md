# Spec: Wire /faceswap bot flow to our ComfyUI graph on Replicate

**Date:** 2026-06-21
**Status:** Approved (Approach A)
**Context:** Strategy pivot RunPod → Replicate (`jarvis-strategy-pivot-replicate`,
`jarvis-replicate-faceswap-recon`). Step 1 (photo swap) validated end-to-end on
`comfyui/any-comfyui-workflow-a100`. This step wires the existing Telegram
`/faceswap` flow to run OUR ComfyUI graph instead of third-party models.

## Problem

The bot's `/faceswap` flow currently calls third-party Replicate models
(`cdingram/face-swap`, `codeplugtech/face-swap` + GFPGAN) in
`app/services/face_swap.py`. Those external models are the exact dependency that
broke in May 2026 and triggered the whole RunPod effort. We want `/faceswap` to
run our own validated ComfyUI graph (ReActor + inswapper_128 + GFPGAN) so we own
the pipeline and the quality path (GPEN/occlusion later).

## Goal

`/faceswap` in Telegram → our `face_swap_only.json` on
`comfyui/any-comfyui-workflow-a100` → result back in chat. Output must be
**identical** to the validated manual test (inswapper_128 + GFPGANv1.4.pth,
visibility=1.0, version `82c95ab88c3f…e8913`).

## Approach A (chosen)

Add a sync runner to `app/services/face_swap.py` reusing its existing sync
helpers (`_post_prediction`, `poll_replicate`). The bot flow is synchronous, so
keep the backend sync — no async/sync bridging. The async
`faceswap_client.FaceSwapProvider` stays as the canonical runner for the future
LLM-router tool + video path; the version id is imported from it so there is a
single source of truth.

### Data flow

```
/faceswap → source(step1) → target(step2) → [single button] (step3)
  conv.data{source_url, target_url}            # URLs keyed by full file_id — B-51 safe
   └─ fs:exec:basic → face_swap_basic(source_url, target_url)   # callsite UNCHANGED
        └─ face_swap_comfyui(src, tgt, restore="GFPGANv1.4.pth"):
             download both URLs → zip{source.jpg, target.jpg}
             → face_swap_only.json (strip "_comment", set face_restore_model)
             → POST /v1/predictions {version 82c95ab…, workflow_json,
                                     input_file=zip-data-uri, output_format=png}
             → poll (max_wait 600) → output PNG URL
   └─ send_photo_fn(result)
```

## Changes

### `app/services/face_swap.py`
1. `_get_api_key()` → read `REPLICATE_API_TOKEN`, fall back to
   `REPLICATE_API_KEY` (both exist in `.env`; standardize on persona's TOKEN).
2. New pure helpers (unit-testable, no network):
   - `_load_swap_graph(restore_model)` — load `face_swap_only.json`, drop
     non-dict top-level keys (`_comment`), set `face_restore_model` on the
     `ReActorFaceSwap`/`ReActorFaceSwapOpt` node.
   - `_zip_two(source_bytes, target_bytes)` — in-memory zip with arcnames
     `source.jpg` / `target.jpg` (matching the graph's LoadImage filenames).
   - `_build_swap_payload(graph, zip_bytes)` — `{version: _FACESWAP_VERSION,
     input: {workflow_json: json.dumps(graph), input_file: "data:application/
     zip;base64,…", output_format: "png", return_temp_files: False}}`.
3. New `face_swap_comfyui(source_url, target_url, restore_model="GFPGANv1.4.pth")`
   — download both URLs (sync urllib), zip, build payload, `_post_prediction`
   (version-based, to `_PREDICTIONS_BASE`), `poll_replicate(..., max_wait=600)`.
4. Re-point: `face_swap_basic` → `face_swap_comfyui(s, t, "GFPGANv1.4.pth")`;
   `face_swap_with_polish` → same for now (defaults), GPEN-1024 swap is the
   immediate next step (one-field change to `"GPEN-BFR-1024.onnx"`).
5. `_FACESWAP_VERSION` imported from `faceswap_client` (single source of truth).
6. Money safety: `poll_replicate` raises `PredictionFailed` (subclass of
   RuntimeError, so existing `except RuntimeError` still catches) on
   failed/canceled. There is NO retry loop around predictions in this file, so a
   failed/billable prediction raises once — never re-run.
7. Old third-party path (`_FACE_SWAP_VERSION`, `_FACE_SWAP_REACTOR_VERSION`,
   `face_swap_reactor`, original bodies) kept under a
   `# RESERVE (off critical path — pivot 2026-06-21)` comment block. NOT deleted.

### `tools/photo_studio_telegram.py`
8. `_confirm_faceswap_keyboard()` collapses to a single action button
   `🔄 Сделать swap (~$0.04)` (callback `fs:exec:basic`) + `❌ Отмена`. Two
   identical tiers would mislead the user until GPEN lands.
9. `handle_faceswap_callback` cost string: basic → `$0.04` (was `$0.005`). The
   `polish` branch stays in code (dormant, no button) for when GPEN lands.
   Steps source→target and cancel are unchanged.

## Error handling
`face_swap_comfyui` surfaces clear errors (download failure, Replicate 4xx,
`PredictionFailed`). The existing `try/except` in `handle_faceswap_callback`
catches them and sends `❌ Ошибка face swap: …`. No retries on billable failures.

## Testing
1. **Unit (no network, free):** `tests/test_faceswap_comfyui_payload.py` —
   `_load_swap_graph` strips `_comment` (4 nodes remain) and sets restore model
   on the ReActor node; `_build_swap_payload` has correct version, `input_file`
   data-uri prefix, `workflow_json` parses to a dict containing `ReActorFaceSwap`,
   `output_format == "png"`; `_zip_two` yields a valid zip with both arcnames.
2. **Manual:** one real `/faceswap` in Telegram on the same two photos
   (brunette source → blonde target) → expect brunette face in the car scene,
   ~25–30s, ~$0.04.

## B-51 note
The `/faceswap` flow stores Telegram photo URLs (`get_telegram_photo_url(file_id)`,
full file_id) in conversation state per step — not the truncated saved filename
`photo_<file_id[:8]>.jpg`. Distinct photos → distinct file_ids → distinct URLs.
The B-51 overwrite collision does not affect this flow (it only hit the manual
generic-save path).

## Out of scope (later steps)
- GPEN-BFR-1024 for the Polished tier (re-introduce 2nd button).
- Occlusion / video (`video_face_swap.json`, ReActorMaskHelper).
- LLM-router tool (`build_faceswap_tool` + `register_default_tools`).
- `/me_into` (reuses the same backend → benefits automatically).

## Safeguards (explicit, per user)
1. Identical output to the validated run: same version, inswapper+GFPGAN, vis=1.0.
2. Keep `PredictionFailed` no-retry semantics in the sync path.
3. Button price updated to `$0.04` (real cost).
4. Unit test first (free), then one manual Telegram test before "done".
5. Old cdingram/codeplugtech kept in reserve under comment, not deleted.
