#!/usr/bin/env python3
"""Phase 3.2: terminate broken pod, start fresh on A100 (EU-RO-1), print SSH string.

Retry loop: MAX_PASSES attempts, DELAY_SEC between each.
EU-RO-1 GPU chain: A100 PCIe -> A100 SXM4.
"""
import asyncio, sys, logging, signal, time
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from app.services.block_m2_video.runpod.runpod_client import (
    RunpodClient, RunpodApiError, RunpodSupplyError,
)
from app.services.block_m2_video.runpod.runpod_config import get_runpod_config

logging.basicConfig(level=logging.WARNING)

BROKEN_IP        = "213.173.108.42"
MAX_PASSES       = 10
DELAY_SEC        = 180
MAX_TOTAL_SEC    = MAX_PASSES * DELAY_SEC  # 30 min

EU_RO1_GPU_CHAIN = [
    "NVIDIA A100 80GB PCIe",
    "NVIDIA A100-SXM4-80GB",
]

_interrupted = False


def _sigint_handler(signum, frame):
    global _interrupted
    _interrupted = True
    print("\\n[sigint] Ctrl+C received - will exit after current operation")


def _has_ip(pod, ip: str) -> bool:
    for p in ((pod.runtime or {}).get("ports") or []):
        if p.get("ip") == ip:
            return True
    return False


def _ssh_info(pod):
    for p in ((pod.runtime or {}).get("ports") or []):
        if p.get("privatePort") == 22 and p.get("isIpPublic"):
            return p.get("ip"), p.get("publicPort")
    return None, None


async def _interruptible_sleep(seconds: int, label: str) -> bool:
    """Sleep in 1s chunks; return False if interrupted."""
    print(f"[retry] {label} - sleeping {seconds}s (Ctrl+C to abort) ...", flush=True)
    for i in range(seconds):
        if _interrupted:
            return False
        await asyncio.sleep(1)
        if (i + 1) % 30 == 0:
            print(f"[retry]   ... {i + 1}s / {seconds}s", flush=True)
    return True


async def _run():
    signal.signal(signal.SIGINT, _sigint_handler)
    sys.stdout.reconfigure(line_buffering=True)
    print("[init] starting, loading config...", flush=True)
    config = get_runpod_config()
    print("[init] config loaded, connecting to RunPod API...", flush=True)
    run_start = time.monotonic()

    async with RunpodClient(config=config) as client:

        # ── 1. Terminate broken pod ───────────────────────────────────
        print("[terminate] scanning pods for broken IP...", flush=True)
        pods = await client.list_pods()
        for pod in pods:
            if _has_ip(pod, BROKEN_IP):
                print(f"[terminate] found pod {pod.id} (status={pod.desired_status})")
                await client.terminate_pod(pod.id)
                for i in range(12):
                    await asyncio.sleep(5)
                    p = await client.get_pod(pod.id)
                    if p is None or (p.desired_status or "").upper() in ("EXITED", "TERMINATED"):
                        print(f"[terminate] confirmed after {(i+1)*5}s")
                        break
                else:
                    print("[terminate] WARNING: not terminated after 60s, proceeding")
                break
        else:
            print("[terminate] no pod with broken IP found, skipping")

        # ── 2. Retry loop ─────────────────────────────────────────────
        ts   = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        name = f"jarvis-p32-{ts}"
        print(f"[start] pod name: {name}")
        print(f"        volume={config.network_volume_id}  dc={config.datacenter}")
        print(f"        GPU chain: {EU_RO1_GPU_CHAIN}")
        print(f"        max {MAX_PASSES} passes x {DELAY_SEC}s = {MAX_TOTAL_SEC//60} min total")

        pod = None
        for pass_num in range(1, MAX_PASSES + 1):
            elapsed_min = (time.monotonic() - run_start) / 60
            max_min     = MAX_TOTAL_SEC / 60
            print(f"\\n[retry] === pass {pass_num}/{MAX_PASSES}  elapsed: {elapsed_min:.1f} min / {max_min:.0f} max ===")

            for gpu_id in EU_RO1_GPU_CHAIN:
                if _interrupted:
                    print("[sigint] interrupted by user, no pod created")
                    sys.exit(130)
                print(f"[start] trying gpu={gpu_id} ...")
                try:
                    pod = await client.start_pod(
                        name=name,
                        gpu_type_id=gpu_id,
                    )
                    print(f"[start] SUCCESS: gpu={gpu_id}  pod={pod.id}")
                    break
                except RunpodSupplyError as e:
                    print(f"[start] SUPPLY_CONSTRAINT: {gpu_id}", flush=True)
                except RunpodApiError as e:
                    print(f"[start] API ERROR on {gpu_id}: {e}", flush=True)
                    raise
                except Exception as e:
                    print(f"[start] UNEXPECTED ERROR on {gpu_id}: {type(e).__name__}: {e}", flush=True)
                    raise

            if pod is not None:
                break  # success - exit pass loop

            # All GPUs exhausted this pass
            if pass_num == MAX_PASSES:
                elapsed_min = (time.monotonic() - run_start) / 60
                print(f"\\n[retry] pass {pass_num}/{MAX_PASSES} exhausted ({elapsed_min:.1f} min elapsed)")
                print("ERROR: EU-RO-1 fully drained for 30 minutes, supply problem - try again later",
                      file=sys.stderr)
                sys.exit(1)

            elapsed_min = (time.monotonic() - run_start) / 60
            label = f"pass {pass_num}/{MAX_PASSES} exhausted ({elapsed_min:.1f} min / {max_min:.0f} max)"
            ok = await _interruptible_sleep(DELAY_SEC, label)
            if not ok:
                print("[sigint] interrupted by user, no pod created")
                sys.exit(130)

        # ── 3. Wait for RUNNING ───────────────────────────────────────
        print(f"\\n[wait] polling pod {pod.id} for RUNNING (timeout=300s) ...")
        ready = await client.wait_for_ready(pod.id, timeout_sec=300)
        print(f"[wait] RUNNING: {ready.id}")

        # ── 4. Extract SSH info (retry 7x / 70s) ─────────────────────
        for attempt in range(7):
            host, port = _ssh_info(ready)
            if host and port:
                print(f"[ready] SSH info after {attempt * 10}s extra wait")
                print(f"ssh root@{host} -p {port}")
                return
            if attempt < 6:
                await asyncio.sleep(10)
                ready = await client.get_pod(pod.id)

        print(f"ERROR: SSH ports not in runtime after 70s. pod={ready.id}",
              file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(_run())