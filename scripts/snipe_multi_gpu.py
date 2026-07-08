# -*- coding: utf-8 -*-
"""Catch the FIRST available capable GPU among a list, WITH the RunPod template
(so /workspace/bootstrap.sh runs -> full reinstall -> ComfyUI), then wait for
ComfyUI to become ready. Leaves the pod RUNNING for the face-swap test.

Reuses the tested provisioner (pod_provisioner.wait_for_pod_ready). Unlike the
A100-only sniper, this tries N GPU types per round so it catches whatever frees
first under EU-RO-1 supply pressure.

Run: C:\\jarvis\\.venv\\Scripts\\python.exe scripts/snipe_multi_gpu.py
Status: state/multi_sniper_status.json
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from app.services.block_m2_video.runpod.runpod_client import (  # noqa: E402
    RunpodClient,
    RunpodSupplyError,
)
from app.services.block_m2_video.runpod.runpod_config import (  # noqa: E402
    get_runpod_config,
)
from app.services.block_m2_video.runpod.pod_provisioner import (  # noqa: E402
    ProvisionOutcome,
    wait_for_pod_ready,
)

# First-available wins. Capable for ReActor video; A100 included per request.
_CANDIDATES = (
    "NVIDIA RTX A6000",
    "NVIDIA A40",
    "NVIDIA A100 80GB PCIe",
    "NVIDIA A100-SXM4-80GB",
)
_STATUS = _ROOT / "state" / "multi_sniper_status.json"
_DEADLINE_MIN = 120
_ROUND_SLEEP = 20


def _write(d: dict) -> None:
    try:
        _STATUS.write_text(json.dumps(d, indent=2), encoding="utf-8")
    except Exception:
        pass


async def main() -> int:
    cfg = get_runpod_config()
    cfg.gpu_fallback_id = None  # drive our own list; KEEP template_id for ComfyUI
    name = f"jarvis-swap-multi-{int(time.time())}"
    client = RunpodClient(config=cfg)
    start = time.monotonic()
    deadline = start + _DEADLINE_MIN * 60
    rnd = 0
    print(f"[multi-snipe] start name={name} candidates={list(_CANDIDATES)} "
          f"deadline={_DEADLINE_MIN}min", flush=True)
    _write({"status": "polling", "round": 0, "candidates": list(_CANDIDATES),
            "pod_name": name})

    async with client:
        pod = None
        while time.monotonic() < deadline:
            rnd += 1
            for gpu in _CANDIDATES:
                try:
                    pod = await client.start_pod(name=name, gpu_type_id=gpu)
                    print(f"[multi-snipe] CAUGHT {gpu} pod={pod.id} round={rnd}",
                          flush=True)
                    _write({"status": "caught", "gpu": gpu, "pod_id": pod.id,
                            "round": rnd, "pod_name": name})
                    break
                except RunpodSupplyError:
                    continue
            if pod is not None:
                break
            print(f"[multi-snipe] round {rnd}: all {len(_CANDIDATES)} GPUs "
                  f"supply-constrained; sleeping {_ROUND_SLEEP}s", flush=True)
            _write({"status": "polling", "round": rnd,
                    "candidates": list(_CANDIDATES), "pod_name": name})
            await asyncio.sleep(_ROUND_SLEEP)

        if pod is None:
            print("[multi-snipe] TIMEOUT: no GPU caught within deadline", flush=True)
            _write({"status": "timeout", "rounds": rnd, "pod_name": name})
            return 2

        print(f"[multi-snipe] waiting for ComfyUI on {pod.id} (up to 25min)...",
              flush=True)
        _write({"status": "provisioning", "pod_id": pod.id, "pod_name": name})
        result = await wait_for_pod_ready(client, pod)
        print(f"[multi-snipe] provision: {result.outcome.value} | "
              f"{result.public_url} | {result.detail}", flush=True)
        _write({
            "status": "provisioned",
            "pod_id": pod.id,
            "pod_name": name,
            "url": result.public_url,
            "outcome": result.outcome.value,
            "detail": result.detail,
            "elapsed_sec": result.elapsed_sec,
        })
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
