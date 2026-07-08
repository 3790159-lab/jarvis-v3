# -*- coding: utf-8 -*-
"""One-shot: deploy the repo's scripts/remote/bootstrap_pod.sh onto the network
volume as /workspace/bootstrap.sh, then force the next real pod to reinstall.

Why: pods run /workspace/bootstrap.sh FROM THE VOLUME (manual deploy). The repo
fix (onnxruntime-gpu enforce + occlusion models, version 2026.06.16-001) is NOT
live until the volume copy is replaced — stale volume = CPU onnxruntime regression.

Strategy:
  1. Spawn a cheap patch pod (raw image, NO template so the broken/old startCmd
     does not run), volume mounted at /workspace.
  2. Try podExec to: write the new bootstrap (base64, no quoting headaches),
     chmod +x, and `rm -f /workspace/.bootstrap_version` so the next real pod
     does a clean full reinstall. Then read back proof (version line, file size).
  3. ALWAYS stop the pod on the happy path (no cost leak). If podExec is
     unavailable on this account, LEAVE the pod running and print exact SSH
     paste commands + the pod id and stop command for manual cleanup.

Supersedes scripts/deploy_patch_pod.py (which had no auto-stop = cost leak).
Run: C:\\jarvis\\.venv\\Scripts\\python.exe scripts/deploy_bootstrap_to_volume.py
"""
from __future__ import annotations

import asyncio
import base64
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from app.services.block_m2_video.runpod.runpod_client import (  # noqa: E402
    RunpodClient,
    RunpodExecUnavailable,
    RunpodSupplyError,
)
from app.services.block_m2_video.runpod.runpod_config import (  # noqa: E402
    get_runpod_config,
)

_BOOTSTRAP_SRC = _ROOT / "scripts" / "remote" / "bootstrap_pod.sh"
_READY_TIMEOUT_SEC = 180
_POLL_SEC = 10

# The deploy pod only writes a file to the volume — it needs ANY cheap GPU
# available in the volume's datacenter (NOT the A100 the real test needs).
# Tried in order; only a successful spawn bills (supply failures are free).
_DEPLOY_GPU_CANDIDATES = (
    "NVIDIA RTX A4000",   # ~$0.25/hr, 16GB — common in EU-RO-1
    "NVIDIA RTX A4500",   # ~$0.27/hr, 20GB
    "NVIDIA RTX A5000",   # ~$0.27/hr, 24GB
    "NVIDIA A40",         # ~$0.44/hr, 48GB
    "NVIDIA RTX A6000",   # ~$0.49/hr, 48GB
)


async def _spawn_cheapest(client) -> object:
    """Try cheap GPUs in order; return the first PodInfo that deploys."""
    last_exc = None
    for gpu in _DEPLOY_GPU_CANDIDATES:
        try:
            print(f"  trying GPU '{gpu}'...")
            pod = await client.start_pod(
                name="jarvis-deploy-bootstrap",
                gpu_type_id=gpu,
                image_name=client._config.docker_image,
                ports="22/tcp",
                container_disk_in_gb=10,
            )
            print(f"  spawned on '{gpu}': {pod.id}")
            return pod
        except RunpodSupplyError as exc:
            print(f"  '{gpu}' unavailable, next...")
            last_exc = exc
    raise RunpodSupplyError(f"no deploy GPU available in datacenter (last: {last_exc})")


def _remote_cmd(b64: str) -> str:
    """Single shell command: decode bootstrap to the volume, reset version, prove it."""
    return (
        f"echo '{b64}' | base64 -d > /workspace/bootstrap.sh && "
        "chmod +x /workspace/bootstrap.sh && "
        "rm -f /workspace/.bootstrap_version && "
        "echo '=== DEPLOYED ===' && "
        "grep -m1 BOOTSTRAP_VERSION= /workspace/bootstrap.sh && "
        "wc -l /workspace/bootstrap.sh && "
        "ls -l /workspace/bootstrap.sh && "
        "(test -f /workspace/.bootstrap_version "
        "&& echo 'WARN version file still present' "
        "|| echo 'OK version file removed -> next pod reinstalls')"
    )


async def main() -> int:
    src_text = _BOOTSTRAP_SRC.read_text(encoding="utf-8")
    b64 = base64.b64encode(src_text.encode("utf-8")).decode("ascii")
    print(f"Local bootstrap: {_BOOTSTRAP_SRC} ({len(src_text)} bytes, "
          f"{src_text.count(chr(10)) + 1} lines)")

    cfg = get_runpod_config()
    cfg.template_id = None  # CRITICAL: raw image, do NOT run the old startCmd
    cfg.gpu_fallback_id = None  # we drive our own cheap-GPU fallback list
    client = RunpodClient(cfg)

    pod_id = None
    keep_alive = False
    async with client:
        print(f"Spawning patch pod (volume {cfg.network_volume_id}, dc {cfg.datacenter})...")
        pod = await _spawn_cheapest(client)
        pod_id = pod.id
        print(f"Pod: {pod_id} — waiting up to {_READY_TIMEOUT_SEC}s for RUNNING...")
        try:
            waited = 0
            while waited < _READY_TIMEOUT_SEC:
                info = await client.get_pod(pod_id)
                status = (info.desired_status or "").upper() if info else ""
                rt = info.runtime if info else None
                if status == "RUNNING" and rt:
                    print(f"  RUNNING after {waited}s (runtime ready)")
                    break
                await asyncio.sleep(_POLL_SEC)
                waited += _POLL_SEC
            else:
                print("  WARN: not confirmed RUNNING within timeout; trying podExec anyway")

            print("Attempting podExec deploy...")
            try:
                res = await client.execute_command(pod_id, _remote_cmd(b64))
                print(f"--- podExec output (exit={res.exit_code}) ---")
                print(res.output)
                print("--- end ---")
                if res.exit_code == 0 and "=== DEPLOYED ===" in res.output:
                    print("\n[OK] DEPLOY OK via podExec. Stopping pod.")
                else:
                    print("\n[WARN] podExec ran but output looks wrong - review above. Stopping pod.")
            except RunpodExecUnavailable as exc:
                keep_alive = True
                print(f"\n[FAIL] podExec unavailable on this account: {exc}")
                print("=> Pod left RUNNING for manual SSH. In the RunPod web terminal / SSH, run:\n")
                print("cat <<'B64' | base64 -d > /workspace/bootstrap.sh")
                print(b64)
                print("B64")
                print("chmod +x /workspace/bootstrap.sh && rm -f /workspace/.bootstrap_version")
                print("grep -m1 BOOTSTRAP_VERSION= /workspace/bootstrap.sh && wc -l /workspace/bootstrap.sh\n")
                print(f"!! REMEMBER TO STOP THE POD AFTER: pod_id = {pod_id} (it is billing ~$1.39/hr)")
        finally:
            if pod_id and not keep_alive:
                ok = await client.stop_pod(pod_id)
                print(f"Pod {pod_id} stop requested: {ok}")
            elif keep_alive:
                print(f"Pod {pod_id} LEFT RUNNING (manual deploy + manual stop required).")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
