---
category: "issues"
created: "2026-06-17"
tags: ["jarvis", "issues", "bug", "face-swap", "runpod", "performance", "bootstrap", "critical"]
bug_id: "B-52"
severity: "high"
priority: "next"
status: "open"
related: []
---
# B-52 — 🔴 bootstrap installs onnxruntime inconsistently (CPU vs GPU) → ReActor swap runs on CPU, A100 idle

## Summary
`scripts/remote/bootstrap_pod.sh` provisions ComfyUI/ReActor on RunPod pods
**non-deterministically with respect to the onnxruntime backend**. Some pods come
up with `onnxruntime-gpu` (ReActor/insightface use `CUDAExecutionProvider` →
fast, ~0.1–0.3 s/frame). Others come up with the CPU-only `onnxruntime` →
**every frame's face detect + inswapper swap runs on the CPU**, ~5 s/frame
(20–40× slower), while the A100 sits at **0 % utilisation**. This explains the
previously-"random" face-swap speed across pods.

## Evidence (observed 2026-06-17, pod `us24ohc20a65ty`)
- RunPod live metrics during a video swap: **CPU 100 %, GPU 0 %**.
- ComfyUI `/system_stats`: **vram_free ≈ 81 GB of 85 GB** — i.e. the swap is
  running with the GPU essentially empty (nothing loaded for inference).
- websocket progress: node `3` (ReActor swap) advancing at **~25–27 frames/min
  ≈ ~2.4–5 s/frame** on a 415-frame clip (est. was 6.2 min; real-world far longer).
- **Same clip + same face** ran fast on earlier pods → not input-dependent; the
  variable is which onnxruntime backend bootstrap happened to install.
- Slow node is **node 3 (the swap)**, NOT node 5 (`ReActorMaskHelper`/SAM) — the
  occlusion feature is exonerated; the mask node had not even started.
- Repro prompt id: `afab5239-8bcc-411c-abb7-4d2e34044225`.

## Root cause (suspected)
`bootstrap_pod.sh` does not pin/guarantee `onnxruntime-gpu` (and remove any
CPU `onnxruntime` that a transitive dep may pull in). When the CPU wheel wins
the resolution, `onnxruntime.get_available_providers()` lacks
`CUDAExecutionProvider`, so insightface/ReActor silently fall back to CPU. No
error is raised — it just runs slow with the GPU idle.

## Suspected location
`scripts/remote/bootstrap_pod.sh` — the pip/install section that sets up
ReActor / insightface / onnxruntime.

## Severity — High
Not a correctness bug (swaps still produce correct output), but a **20–40×
performance + cost regression**: A100 time billed while the GPU does nothing,
and user-facing swaps can take 30+ min instead of minutes. Makes the
occlusion/video-swap features feel broken/unusable on an unlucky pod.

## Priority — Next (not deferred)
Random, hard-to-diagnose slowness that burns money every time it hits. Fix
before relying on video swap / occlusion in production.

## Suggested fix direction (not implemented this session)
1. In `bootstrap_pod.sh`: explicitly `pip uninstall -y onnxruntime` then
   `pip install onnxruntime-gpu==<pinned>` (order matters — CPU wheel shadows GPU
   if both present), matched to the pod's CUDA version.
2. Add a **startup assertion**: fail the bootstrap (or log loudly) if
   `'CUDAExecutionProvider' not in onnxruntime.get_available_providers()`.
3. Optionally surface the active provider from the engine at swap start so a
   CPU-fallback pod is flagged in logs instead of silently running slow.
4. Re-test the same 415-frame clip; expect node 3 to drop from ~5 s/frame to
   sub-second/frame and GPU util > 0 %.
