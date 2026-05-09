# -*- coding: utf-8 -*-
"""Phase 3.0 RunPod inventory script.

Launches a real Pod with the cheapest available GPU, attempts to inventory
the contents of the network volume, and **always** stops the Pod in a
``finally`` block. Costs are recorded to ``state/runpod/billing.jsonl`` so
the guardian can see them.

Run from project root:

    python scripts/runpod_inventory.py

The script will print a plan and require typing ``yes`` before any Pod is
created — this is the only safety barrier against an accidental launch.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Allow running as `python scripts/runpod_inventory.py` from project root.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.core.logging_setup import setup_app_logging  # noqa: E402
from app.services.block_m2_video.runpod.runpod_client import (  # noqa: E402
    GpuType,
    PodInfo,
    RunpodApiError,
    RunpodClient,
    RunpodExecUnavailable,
)
from app.services.block_m2_video.runpod.runpod_config import (  # noqa: E402
    RunpodConfig,
    get_runpod_config,
)

logger = logging.getLogger("runpod_inventory")

_INVENTORY_CMD = (
    "echo '=== ComfyUI ===' && ls -la /workspace/ComfyUI 2>/dev/null | head -5; "
    "echo '=== Models size ===' && du -sh /workspace/ComfyUI/models 2>/dev/null; "
    "echo '=== Diffusion models ===' "
    "&& ls -lh /workspace/ComfyUI/models/diffusion_models 2>/dev/null; "
    "echo '=== Text encoders ===' "
    "&& ls -lh /workspace/ComfyUI/models/text_encoders 2>/dev/null; "
    "echo '=== VAE ===' && ls -lh /workspace/ComfyUI/models/vae 2>/dev/null; "
    "echo '=== Custom nodes ===' "
    "&& ls /workspace/ComfyUI/custom_nodes 2>/dev/null "
    "| grep -v __pycache__ | head -20; "
    "echo '=== Workflows ===' "
    "&& find /workspace/ComfyUI/user 2>/dev/null -name '*.json' | head -10; "
    "echo '=== Wan2.2 search ===' "
    "&& find /workspace -name '*wan*' -o -name '*Wan*' 2>/dev/null | head -20; "
    "echo '=== Disk free ===' && df -h /workspace"
)

_STATE_DIR = _ROOT / "state" / "runpod"
_RUNS_DIR = _STATE_DIR / "inventory_runs"


# ────────────────────────────────────────────────────────────────────────────
# GPU selection
# ────────────────────────────────────────────────────────────────────────────


def _gpu_lowest_price(gpu: GpuType) -> float | None:
    """Return the lowest non-null price for a GPU, ignoring null/zero entries."""
    candidates = [
        p for p in (gpu.community_price, gpu.secure_price) if p and p > 0
    ]
    return min(candidates) if candidates else None


def _pick_cheapest_gpu(
    gpus: list[GpuType], *, preferred_id: str | None
) -> GpuType:
    """Pick the cheapest GPU offering by published per-hour price.

    If ``preferred_id`` matches an entry, prefer it over price as long as it
    has at least one published price (so we know it's bookable).
    """
    if preferred_id:
        for gpu in gpus:
            if gpu.id == preferred_id and _gpu_lowest_price(gpu) is not None:
                return gpu

    priced = [(gpu, _gpu_lowest_price(gpu)) for gpu in gpus]
    priced = [(gpu, price) for gpu, price in priced if price is not None]
    if not priced:
        raise RuntimeError("No GPUs with published prices available")
    priced.sort(key=lambda item: item[1])
    return priced[0][0]


# ────────────────────────────────────────────────────────────────────────────
# Pretty printing & user confirmation
# ────────────────────────────────────────────────────────────────────────────


def _print_plan(
    config: RunpodConfig,
    gpu: GpuType,
    *,
    image: str,
    container_disk_gb: int,
    estimated_cost_usd: float,
) -> None:
    price = _gpu_lowest_price(gpu)
    print("\n" + "=" * 72)
    print("RunPod Phase 3.0 inventory plan")
    print("=" * 72)
    print(f"  Datacenter           : {config.datacenter}")
    print(f"  Network volume       : {config.network_volume_id}")
    print(f"  GPU id               : {gpu.id}")
    print(f"  GPU name             : {gpu.display_name}")
    print(f"  GPU price (per hour) : ${price:.3f}" if price else "  GPU price            : ?")
    print(f"  Container image      : {image}")
    print(f"  Container disk (GB)  : {container_disk_gb}")
    print(f"  Lifetime ceiling     : {config.max_pod_lifetime_min} min "
          f"(guardian will force-stop)")
    print(f"  Estimated cost       : ~${estimated_cost_usd:.3f} "
          f"for a 5-minute run")
    print("=" * 72)
    print(
        "The script will:\n"
        "  1. Start the Pod with the network volume mounted at /workspace.\n"
        "  2. Wait up to 180s for status RUNNING.\n"
        "  3. Try to inventory the volume via podExec (may not be available).\n"
        "  4. ALWAYS stop the Pod in a finally block.\n"
        "  5. Verify the Pod is EXITED/TERMINATED and log the cost.\n"
    )


def _confirm() -> bool:
    answer = input("Type 'yes' to start Pod (will cost ~$0.05-0.30): ").strip().lower()
    return answer == "yes"


# ────────────────────────────────────────────────────────────────────────────
# Wait helpers
# ────────────────────────────────────────────────────────────────────────────


async def _wait_for_ready_with_progress(
    client: RunpodClient, pod_id: str, *, timeout_sec: int
) -> PodInfo:
    """Poll ``get_pod`` and log progress every ~10s."""
    deadline = time.time() + timeout_sec
    last_status: str | None = None
    last_log = 0.0
    while time.time() < deadline:
        pod = await client.get_pod(pod_id)
        now = time.time()
        if pod is not None:
            if pod.desired_status != last_status or (now - last_log) > 10:
                logger.info(
                    "Pod %s status=%s (waited %.0fs)",
                    pod_id,
                    pod.desired_status,
                    timeout_sec - (deadline - now),
                )
                last_status = pod.desired_status
                last_log = now
            if (pod.desired_status or "").upper() == "RUNNING":
                return pod
        await asyncio.sleep(3)
    raise RunpodApiError(
        f"pod {pod_id} did not reach RUNNING within {timeout_sec}s "
        f"(last_status={last_status})",
        query_name="wait_for_ready",
    )


# ────────────────────────────────────────────────────────────────────────────
# Inventory + Stop
# ────────────────────────────────────────────────────────────────────────────


def _ssh_hint(pod: PodInfo) -> str:
    """Best-effort SSH hint for the user when auto-exec is unavailable."""
    return (
        f"Pod id: {pod.id}\n"
        f"Open RunPod console -> Pods -> {pod.name or pod.id}\n"
        f"Click 'Connect' for the SSH command. Then run:\n"
        f"  {_INVENTORY_CMD}\n"
        f"Save the output before re-running this script "
        f"(this run will stop the Pod momentarily)."
    )


async def _inventory_via_exec(
    client: RunpodClient, pod_id: str, raw_log: Path
) -> bool:
    """Run the inventory command via podExec. Returns True if it produced output."""
    try:
        result = await client.execute_command(pod_id, _INVENTORY_CMD)
    except RunpodExecUnavailable as exc:
        logger.warning("podExec unavailable: %s", exc)
        return False
    except RunpodApiError as exc:
        logger.error("podExec failed: %s", exc)
        return False

    raw_log.write_text(result.output, encoding="utf-8")
    logger.info(
        "Inventory captured (%d chars, exit_code=%s) -> %s",
        len(result.output),
        result.exit_code,
        raw_log,
    )
    print("\n" + "─" * 72)
    print("Inventory output")
    print("─" * 72)
    print(result.output)
    print("─" * 72)
    return True


async def _guaranteed_stop(
    client: RunpodClient, pod: PodInfo | None
) -> dict:
    """Stop the pod and verify status. Returns a structured result for logging."""
    summary: dict = {"pod_id": None, "stop_called": False, "stop_ok": None,
                     "final_status": None}
    if pod is None:
        logger.warning("No pod to stop (start_pod did not complete)")
        return summary
    summary["pod_id"] = pod.id
    summary["stop_called"] = True
    try:
        ok = await client.stop_pod(pod.id)
        summary["stop_ok"] = ok
        if ok:
            logger.info("STOP succeeded for pod %s", pod.id)
        else:
            logger.critical(
                "STOP returned False for pod %s — Pod may still be running!",
                pod.id,
            )
    except Exception as exc:  # noqa: BLE001
        logger.critical("STOP FAILED for pod %s: %s", pod.id, exc)
        summary["stop_ok"] = False

    # Verify
    try:
        await asyncio.sleep(5)
        final = await client.get_pod(pod.id)
        if final is None:
            summary["final_status"] = "NOT_FOUND"
            logger.info("Pod %s no longer visible — assumed terminated", pod.id)
        else:
            summary["final_status"] = final.desired_status
            if (final.desired_status or "").upper() in (
                "EXITED", "TERMINATED", "STOPPED"
            ):
                logger.info(
                    "Pod %s confirmed stopped (status=%s)",
                    pod.id,
                    final.desired_status,
                )
            else:
                logger.critical(
                    "Pod %s still in status %s after stop — manual action needed!",
                    pod.id,
                    final.desired_status,
                )
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not verify final status for %s: %s", pod.id, exc)
    return summary


def _record_billing(
    pod_id: str, gpu: GpuType, started_at: float, stopped_at: float
) -> float:
    elapsed_hours = max(0.0, (stopped_at - started_at) / 3600.0)
    rate = _gpu_lowest_price(gpu) or 0.0
    cost = elapsed_hours * rate
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "pod_id": pod_id,
        "gpu_id": gpu.id,
        "elapsed_seconds": round(stopped_at - started_at, 1),
        "rate_per_hour": rate,
        "cost_usd": round(cost, 4),
        "source": "phase3_inventory",
    }
    with (_STATE_DIR / "billing.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    logger.info(
        "Billing recorded: %.0fs at $%.3f/hr = $%.4f",
        entry["elapsed_seconds"],
        rate,
        cost,
    )
    return cost


# ────────────────────────────────────────────────────────────────────────────
# Main flow
# ────────────────────────────────────────────────────────────────────────────


async def _run() -> int:
    config = get_runpod_config()
    _RUNS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_log = _RUNS_DIR / f"{timestamp}.log"
    raw_log = _RUNS_DIR / f"{timestamp}_volume.txt"

    file_handler = logging.FileHandler(run_log, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
        )
    )
    logging.getLogger().addHandler(file_handler)

    pod: PodInfo | None = None
    started_at: float | None = None
    chosen_gpu: GpuType | None = None
    image = config.docker_image
    container_disk_gb = 5

    client = RunpodClient(config=config)
    try:
        logger.info("Listing GPU offerings…")
        gpus = await client.list_gpu_types()
        chosen_gpu = _pick_cheapest_gpu(gpus, preferred_id=None)
        rate = _gpu_lowest_price(chosen_gpu) or 0.0
        estimated = rate * (5 / 60.0)  # 5-min run

        _print_plan(
            config,
            chosen_gpu,
            image=image,
            container_disk_gb=container_disk_gb,
            estimated_cost_usd=estimated,
        )

        if not _confirm():
            logger.info("Confirmation not given, exiting without launch.")
            print("Aborted.")
            return 1

        logger.info("Starting Pod with GPU=%s (%s)…",
                    chosen_gpu.id, chosen_gpu.display_name)
        pod = await client.start_pod(
            name=f"jarvis-inventory-{timestamp}",
            gpu_type_id=chosen_gpu.id,
            image_name=image,
            ports="22/tcp,8888/http",
            container_disk_in_gb=container_disk_gb,
            volume_in_gb=0,
        )
        started_at = time.time()
        logger.info("Pod started: id=%s name=%s", pod.id, pod.name)

        logger.info("Waiting for Pod to reach RUNNING (timeout 180s)…")
        ready = await _wait_for_ready_with_progress(
            client, pod.id, timeout_sec=180
        )
        logger.info("Pod RUNNING after ~%.0fs", time.time() - started_at)

        url = await client.get_pod_public_url(pod.id, port=8888)
        if url:
            logger.info("Public URL (Jupyter, port 8888): %s", url)
            print(f"Public URL: {url}")

        ok = await _inventory_via_exec(client, ready.id, raw_log)
        if not ok:
            print("\n" + "!" * 72)
            print("podExec did not work in this account/schema.")
            print("Pod will be stopped in a moment. To inventory by hand next time:")
            print(_ssh_hint(ready))
            print("!" * 72)

        return 0

    except Exception as exc:  # noqa: BLE001
        logger.exception("Inventory run failed: %s", exc)
        return 2

    finally:
        stopped_at = time.time()
        summary = await _guaranteed_stop(client, pod)
        if pod and started_at and chosen_gpu:
            cost = _record_billing(pod.id, chosen_gpu, started_at, stopped_at)
            elapsed = stopped_at - started_at
            print("\n" + "=" * 72)
            print(
                f"Started -> ran for {elapsed:.0f}s -> stopped -> "
                f"final_status={summary.get('final_status')}, cost ~${cost:.4f}"
            )
            print("=" * 72)
        await client.aclose()
        logging.getLogger().removeHandler(file_handler)
        file_handler.close()


def main() -> int:
    setup_app_logging("jarvis_runpod_inventory.log", file_level=logging.DEBUG)
    return asyncio.run(_run())


if __name__ == "__main__":
    sys.exit(main())
