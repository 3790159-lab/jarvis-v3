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
import queue
import signal
import sys
import threading
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
    RunpodSupplyError,
)
from app.services.block_m2_video.runpod.runpod_config import (  # noqa: E402
    RunpodConfig,
    get_runpod_config,
)

logger = logging.getLogger("runpod_inventory")

_INVENTORY_BASH_COMMANDS = (
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

# How long to wait for user input on the SSH-fallback prompt before
# auto-stopping. 15 minutes is generous for manual SSH inventory while
# keeping max cost at <$0.07 on RTX 4000 Ada.
_SSH_PAUSE_TIMEOUT_SEC: int = 15 * 60


def _input_with_timeout(prompt: str, timeout_sec: float) -> str | None:
    """Read a line from stdin with a timeout. Returns ``None`` on timeout.

    Uses a daemon thread so the main thread can poll a queue. Cross-platform;
    works on Windows where ``select()`` on stdin is unavailable. The thread
    is daemonic and outlives a timeout — that's fine, the process exits
    soon after anyway and the OS reaps it.
    """
    result_q: queue.Queue[str] = queue.Queue()

    def _reader() -> None:
        try:
            line = input(prompt)
            result_q.put(line)
        except (EOFError, KeyboardInterrupt):
            result_q.put("")

    thread = threading.Thread(target=_reader, daemon=True)
    thread.start()
    try:
        return result_q.get(timeout=timeout_sec)
    except queue.Empty:
        return None


def _on_sigint(signum, frame) -> None:  # noqa: ARG001
    logger.warning("SIGINT received — finally block will stop the Pod")
    raise KeyboardInterrupt()


# ────────────────────────────────────────────────────────────────────────────
# GPU selection
# ────────────────────────────────────────────────────────────────────────────


def _gpu_lowest_price(gpu: GpuType) -> float | None:
    """Return the lowest non-null price for a GPU, ignoring null/zero entries."""
    candidates = [
        p for p in (gpu.community_price, gpu.secure_price) if p and p > 0
    ]
    return min(candidates) if candidates else None


# Cards verified available in EU-RO-1 via RunPod web UI on 2026-05-09.
# The substring matcher hits both id and display_name; order matters
# (cheapest verified-available card first). Keep the legacy entries at
# the end as fallbacks for other datacenters where they may be in stock.
_PREFERRED_GPU_NAMES: tuple[str, ...] = (
    "RTX 4000 Ada",     # $0.26/hr, EU-RO-1 verified
    "RTX 4090",         # $0.69/hr, EU-RO-1 verified
    "RTX PRO 6000",     # $1.89/hr, EU-RO-1 verified — also our boevoi GPU for Wan2.2
    "RTX A4000",        # legacy fallback
    "RTX 3090",         # legacy fallback
)


def _pick_gpu_candidates(
    gpus: list[GpuType], *, top_n: int = 5
) -> list[tuple[str, str, float]]:
    """Return GPU candidates as ``(id, display_name, price)`` tuples.

    Order: preferred cards (in declared order) first, then the top-N
    cheapest offerings by published per-hour price. Cards with no
    published price are skipped — we can't book what we can't price.
    """
    priced: list[tuple[GpuType, float]] = []
    for gpu in gpus:
        price = _gpu_lowest_price(gpu)
        if price is not None:
            priced.append((gpu, price))
    if not priced:
        raise RuntimeError("No GPUs with published prices available")
    priced.sort(key=lambda item: item[1])

    candidates: list[tuple[GpuType, float]] = []
    seen: set[str] = set()

    def _matches_preferred(gpu: GpuType, needle: str) -> bool:
        haystack = f"{gpu.id} {gpu.display_name or ''}".lower()
        if "sff" in haystack:
            # Exclude small-form-factor variants — they trade VRAM for size
            # and have different supply patterns than the full card.
            return False
        return needle.lower() in haystack

    for needle in _PREFERRED_GPU_NAMES:
        for gpu, price in priced:
            if _matches_preferred(gpu, needle) and gpu.id not in seen:
                candidates.append((gpu, price))
                seen.add(gpu.id)
                break

    for gpu, price in priced[:top_n]:
        if gpu.id not in seen:
            candidates.append((gpu, price))
            seen.add(gpu.id)

    return [(gpu.id, gpu.display_name or gpu.id, price) for gpu, price in candidates]


# ────────────────────────────────────────────────────────────────────────────
# Pretty printing & user confirmation
# ────────────────────────────────────────────────────────────────────────────


def _print_plan(
    config: RunpodConfig,
    candidates: list[tuple[str, str, float]],
    *,
    image: str,
    container_disk_gb: int,
) -> None:
    cheapest_price = min(price for _, _, price in candidates) if candidates else 0.0
    estimated = cheapest_price * (5 / 60.0)
    print("\n" + "=" * 72)
    print("RunPod Phase 3.0 inventory plan")
    print("=" * 72)
    print(f"  Datacenter           : {config.datacenter}")
    print(f"  Network volume       : {config.network_volume_id}")
    print(f"  Container image      : {image}")
    print(f"  Container disk (GB)  : {container_disk_gb}")
    print(f"  Lifetime ceiling     : {config.max_pod_lifetime_min} min "
          f"(guardian will force-stop)")
    print(f"  Estimated cost       : ~${estimated:.3f} "
          f"(cheapest candidate, 5 min)")
    print(f"  GPU candidates       : {len(candidates)} (will be tried in order)")
    for i, (gpu_id, gpu_name, price) in enumerate(candidates, 1):
        print(f"    {i}. {gpu_name}  (${price:.3f}/hr)")
        print(f"       id: {gpu_id}")
    print("=" * 72)
    print(
        "The script will:\n"
        "  1. Try GPU candidates in order; on SUPPLY_CONSTRAINT, advance.\n"
        "  2. Mount the network volume at /workspace on the chosen GPU.\n"
        "  3. Wait up to 180s for status RUNNING.\n"
        "  4. Try to inventory the volume via podExec (may not be available).\n"
        "  5. ALWAYS stop the Pod in a finally block.\n"
        "  6. Verify the Pod is EXITED/TERMINATED and log the cost.\n"
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


def _interactive_ssh_pause(pod: PodInfo, rate_per_hour: float) -> str:
    """Block until user signals done/skip or the timeout fires.

    Called only when ``podExec`` is not available. The Pod is still
    running while the user works in another terminal; the surrounding
    ``try/finally`` will stop it as soon as this function returns.

    Returns one of: ``"done"``, ``"skip"``, ``"timeout"``.
    """
    print()
    print("!" * 72)
    print("podExec did not work in this account/schema.")
    print(
        f"Pod is RUNNING. Open RunPod console → Pods → "
        f"{pod.name or pod.id}"
    )
    print("Click 'Connect' button for the SSH command. Then in SSH run:")
    print()
    print(_INVENTORY_BASH_COMMANDS)
    print()
    print(f"Pod id            : {pod.id}")
    print(f"Current cost rate : ${rate_per_hour:.3f}/hr")
    print(f"Auto-stop timeout : {_SSH_PAUSE_TIMEOUT_SEC // 60} minutes")
    print("Pod will stop automatically if no input received.")
    print("!" * 72)
    print()

    deadline = time.monotonic() + _SSH_PAUSE_TIMEOUT_SEC
    while True:
        remaining = max(0, int(deadline - time.monotonic()))
        if remaining == 0:
            logger.warning(
                "SSH pause timed out after %ds — auto-stopping Pod",
                _SSH_PAUSE_TIMEOUT_SEC,
            )
            return "timeout"
        prompt = (
            f"[~{remaining}s left] Type 'done' to stop the Pod now, "
            f"'skip' for the same: "
        )
        response = _input_with_timeout(
            prompt, timeout_sec=min(30.0, float(remaining))
        )
        if response is None:
            # No input within poll window; loop and re-prompt with new countdown.
            continue
        response = response.strip().lower()
        if response in ("done", "skip", ""):
            return response or "done"
        print(f"Unknown input '{response}'. Use 'done' or 'skip'.")


async def _inventory_via_exec(
    client: RunpodClient, pod_id: str, raw_log: Path
) -> bool:
    """Run the inventory command via podExec. Returns True if it produced output."""
    try:
        result = await client.execute_command(pod_id, _INVENTORY_BASH_COMMANDS)
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
    pod_id: str,
    gpu_id: str,
    rate_per_hour: float,
    started_at: float,
    stopped_at: float,
) -> float:
    elapsed_hours = max(0.0, (stopped_at - started_at) / 3600.0)
    cost = elapsed_hours * rate_per_hour
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "pod_id": pod_id,
        "gpu_id": gpu_id,
        "elapsed_seconds": round(stopped_at - started_at, 1),
        "rate_per_hour": rate_per_hour,
        "cost_usd": round(cost, 4),
        "source": "phase3_inventory",
    }
    with (_STATE_DIR / "billing.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    logger.info(
        "Billing recorded: %.0fs at $%.3f/hr = $%.4f",
        entry["elapsed_seconds"],
        rate_per_hour,
        cost,
    )
    return cost


# ────────────────────────────────────────────────────────────────────────────
# Main flow
# ────────────────────────────────────────────────────────────────────────────


async def _run() -> int:
    signal.signal(signal.SIGINT, _on_sigint)
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
    chosen: tuple[str, str, float] | None = None
    image = config.docker_image
    container_disk_gb = 5

    client = RunpodClient(config=config)
    try:
        logger.info("Listing GPU offerings…")
        gpus = await client.list_gpu_types()
        candidates = _pick_gpu_candidates(gpus)
        if not candidates:
            raise RuntimeError("No bookable GPU candidates found")

        _print_plan(
            config,
            candidates,
            image=image,
            container_disk_gb=container_disk_gb,
        )

        if not _confirm():
            logger.info("Confirmation not given, exiting without launch.")
            print("Aborted.")
            return 1

        last_supply_exc: RunpodSupplyError | None = None
        for gpu_id, gpu_name, gpu_price in candidates:
            logger.info(
                "Trying %s (%s) at $%.3f/hr…", gpu_id, gpu_name, gpu_price
            )
            try:
                pod = await client.start_pod(
                    name=f"jarvis-inventory-{timestamp}",
                    gpu_type_id=gpu_id,
                    image_name=image,
                    ports="22/tcp,8888/http",
                    container_disk_in_gb=container_disk_gb,
                    volume_in_gb=0,
                )
            except RunpodSupplyError as exc:
                last_supply_exc = exc
                logger.warning(
                    "  -> no instances of %s available, trying next", gpu_name
                )
                continue
            chosen = (gpu_id, gpu_name, gpu_price)
            break

        if pod is None:
            if last_supply_exc is not None:
                raise last_supply_exc
            raise RuntimeError("All candidate GPUs are out of stock")

        assert chosen is not None
        started_at = time.time()
        logger.info(
            "Pod started on %s: id=%s name=%s", chosen[1], pod.id, pod.name
        )
        print(f"\n>>> Booked {chosen[1]} at ${chosen[2]:.3f}/hr (pod {pod.id})")

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
            pause_result = _interactive_ssh_pause(ready, chosen[2])
            if pause_result == "timeout":
                logger.error(
                    "Inventory pause timed out — Pod will be stopped by finally"
                )

        return 0

    except Exception as exc:  # noqa: BLE001
        logger.exception("Inventory run failed: %s", exc)
        return 2

    finally:
        stopped_at = time.time()
        summary = await _guaranteed_stop(client, pod)
        if pod and started_at and chosen:
            gpu_id, _gpu_name, rate = chosen
            cost = _record_billing(
                pod.id, gpu_id, rate, started_at, stopped_at
            )
            elapsed = stopped_at - started_at
            print("\n" + "=" * 72)
            print(
                f"Started -> ran for {elapsed:.0f}s -> stopped -> "
                f"final_status={summary.get('final_status')}, cost ~${cost:.4f}"
            )
            print("=" * 72)
        try:
            await client.aclose()
        except Exception as exc:  # noqa: BLE001
            logger.warning("client.aclose() raised: %s", exc)
        logging.getLogger().removeHandler(file_handler)
        file_handler.close()


def main() -> int:
    setup_app_logging("jarvis_runpod_inventory.log", file_level=logging.DEBUG)
    return asyncio.run(_run())


if __name__ == "__main__":
    sys.exit(main())
