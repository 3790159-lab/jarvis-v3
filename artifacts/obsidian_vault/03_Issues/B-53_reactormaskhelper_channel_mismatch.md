---
category: "issues"
created: "2026-06-17"
tags: ["jarvis", "issues", "bug", "face-swap", "occlusion", "reactor-node", "comfyui"]
bug_id: "B-53"
severity: "high"
priority: "next"
status: "open"
related: ["B-52"]
---
# B-53 — ReActorMaskHelper crashes on RGB/RGBA channel mismatch in the mask blend

## Summary
With the occlusion mask enabled (`VIDEO_SWAP_OCCLUSION_MASK=1`), node 5
`ReActorMaskHelper` now **executes** (after the B-52 / ultralytics-import fixes)
but crashes during the final alpha-composite of the mask:

```
RuntimeError: The size of tensor a (3) must match the size of tensor b (4)
at non-singleton dimension 2
  File ".../comfyui-reactor-node/nodes.py", line 1482, in execute
    result[image_index] = pasting * paste_mask + result[image_index] * (1. - paste_mask)
```

3 (RGB) vs 4 (RGBA) channel mismatch: one operand of the blend has an alpha
channel, the other does not. The swap itself (node 3) is fine — only the mask
compositing fails, so **no output video is produced**.

## Context / what's already fixed
This is the **4th** sequential blocker hit while bringing occlusion up on pod
`us24ohc20a65ty` (2026-06-17). Already resolved:
1. `bbox_model_name` missing `bbox/` prefix → fixed in our code
   (`_build_mask_helper_node`).
2. onnxruntime CPU→GPU (**B-52**) → fixed on pod; swap now ~2.92 fps on A100.
3. `NameError: YOLO is not defined` (ultralytics imported after ComfyUI start)
   → fixed by restarting ComfyUI.
4. **This issue** — channel mismatch in the blend (node-internal).

## Root cause (suspected)
The reactor node's mask blend assumes all operands share channel count. One of
`image` (frames from VHS_LoadVideo, node 1) / `swapped_image` (ReActor output,
node 3) / the SAM-derived `paste_mask` carries 4 channels (RGBA) while another
is 3 (RGB). Known comfyui-reactor-node fragility when an input image has an
alpha channel. Repro prompt id: `c5393c92-f573-4e34-bd93-f810d07e6ffc`.

## Suspected location
`/workspace/ComfyUI/custom_nodes/comfyui-reactor-node/nodes.py` ~line 1482
(`ReActorMaskHelper.execute`, the `pasting * paste_mask + result * (1-mask)`
blend).

## Severity — High
Blocks the occlusion feature end-to-end: everything up to the mask works, but
the masked video never renders. Not a data-loss/correctness risk elsewhere.

## Suggested fix direction (not implemented)
1. Inspect the pod's actual `nodes.py` ~1455–1490 to see which operand is RGBA.
2. Likely fix: coerce the image operands to 3 channels before the blend
   (drop alpha, e.g. `[..., :3]`) — or convert the frame input to RGB upstream.
   Patch the node on the pod + restart ComfyUI, then re-test.
3. Consider whether a specific ultralytics / reactor-node version pin avoids it
   (8.4.69 currently installed).
4. Longer term: this kind of pod-side node fragility argues for baking a
   known-good, pinned ComfyUI + reactor-node + ultralytics + onnxruntime-gpu
   image in `bootstrap_pod.sh` (ties into B-52).
