# RunPod Phase 3.0 — Inventory + Stop Reliability

Phase 3.0 is the first time Jarvis launches a real Pod on RunPod.
The goal is to verify three things on a live account, then **stop the
Pod cleanly**:

1. Our `RunpodClient.stop_pod()` actually halts billing on a running Pod.
2. The contents of the `comfyui-wan22` network volume — what models,
   custom nodes, and workflows are already provisioned there.
3. End-to-end behaviour of `start_pod` / `wait_for_ready` /
   `get_pod_public_url` against the production GraphQL endpoint.

Budget for this run: up to **$0.30**, target $0.05–$0.10. The guardian's
`max_pod_lifetime_min=20` is a backstop, not a plan — manual stop should
fire long before that.

## Prerequisites

- `.env.runpod` is populated (real `RUNPOD_API_KEY`, `RUNPOD_NETWORK_VOLUME_ID`,
  `RUNPOD_DATACENTER`).
- Python venv from project root: `python -V` should show 3.14.x.
- Phase 2 tests pass: `pytest tests/test_runpod_*.py` should be all green.

## Running the inventory script

```powershell
# from C:\jarvis
.\.venv\Scripts\python scripts\runpod_inventory.py
```

The script prints a plan (chosen GPU, datacenter, volume, lifetime ceiling,
estimated cost) and **waits for `yes` on stdin** before it touches RunPod.
Anything other than `yes` aborts.

After the run you'll see one of two outcomes:

- **Auto-inventory worked** — the volume listing prints to stdout and is
  saved to `state/runpod/inventory_runs/<timestamp>_volume.txt`.
- **`podExec` not available** — the script logs a warning, prints SSH
  instructions for manual inventory, and still stops the Pod. RunPod's
  public GraphQL schema does not currently document a `podExec`
  mutation, so this is the most likely outcome on first run. To
  inventory by hand on a future run, use the SSH command shown in the
  RunPod web console (Pods → Connect → SSH).

Regardless of outcome, the script:

- Logs everything to `state/runpod/inventory_runs/<timestamp>.log`.
- Stops the Pod in a `finally` block.
- Verifies via `client.get_pod()` that `desiredStatus` is `EXITED`,
  `TERMINATED`, or that the Pod is no longer visible.
- Records cost (`elapsed_seconds × per-hour rate`) to
  `state/runpod/billing.jsonl`. The guardian reads this file when
  computing daily spend.

## Running the lifecycle integration test

The test launches a real Pod and immediately stops it. **Double-gated by
two env vars** to make accidental runs hard:

```powershell
$env:RUNPOD_INTEGRATION_TESTS = "1"
$env:RUNPOD_REAL_POD_TESTS    = "1"
.\.venv\Scripts\pytest tests\test_runpod_pod_lifecycle_integration.py -v -s
```

Without both vars, the test is `SKIPPED`. Like the inventory script, the
test always stops the Pod in `finally`.

## Reading the inventory output

The inventory command runs as a single shell pipeline against
`/workspace`. Look for:

- **`=== ComfyUI ===`** — directory listing. If empty, ComfyUI is not
  installed on the volume yet.
- **`=== Models size ===`** — `du -sh` total. Wan2.2 weights typically
  add up to 30–50 GB; under 1 GB means the volume is mostly empty.
- **`=== Diffusion models ===`** / **`=== Text encoders ===`** /
  **`=== VAE ===`** — per-model size. For Wan2.2 i2v we expect at
  minimum a `wan2_2_*.safetensors` diffusion model plus its text
  encoder.
- **`=== Custom nodes ===`** — directory names under
  `ComfyUI/custom_nodes`. Wan2.2 typically requires
  `ComfyUI-WanVideoWrapper` or similar.
- **`=== Workflows ===`** — JSON files in `ComfyUI/user/...`. If
  there's a `wan*.json` here, copy it to
  `app/services/block_m2_video/runpod/workflows/templates/wan22_i2v.json`
  for Phase 3.1.
- **`=== Wan2.2 search ===`** — a broad `find` across `/workspace`.
- **`=== Disk free ===`** — `df -h /workspace`.

## Manual stop verification

If the script logs `STOP succeeded` and the final status check passes,
no further action is needed. If anything looks wrong:

1. Open the RunPod console → Pods.
2. Find the Pod by name (`jarvis-inventory-...`).
3. If it is still running, click **Stop** and then **Terminate**.
4. Compare `state/runpod/inventory_runs/<timestamp>.log` against the
   console — the log will record exactly what happened.

A `CRITICAL` log line is the signal that automatic stop did not work. The
script always prints a final summary banner with elapsed time, final
status, and recorded cost — keep an eye on that.

## File map

```
scripts/runpod_inventory.py                          # this script
tests/test_runpod_pod_lifecycle_integration.py       # double-gated lifecycle test
state/runpod/inventory_runs/<ts>.log                 # full Python log per run
state/runpod/inventory_runs/<ts>_volume.txt          # raw stdout from podExec
state/runpod/billing.jsonl                           # cost lines (read by guardian)
```
