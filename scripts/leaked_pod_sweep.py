# -*- coding: utf-8 -*-
"""Read-only leaked-pod safety sweep.

Lists every pod on the RunPod account and flags:
  * RUNNING pods  -> leaked COMPUTE (billing $/hr right now)
  * non-running retained pods -> leaked STORAGE (container disk still billed)

Launches NOTHING, stops NOTHING, terminates NOTHING. Pure inventory.
Run from project root:  python scripts/leaked_pod_sweep.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.services.block_m2_video.runpod.runpod_client import RunpodClient  # noqa: E402
from app.services.block_m2_video.runpod.runpod_config import get_runpod_config  # noqa: E402

_RUNNING = {"RUNNING"}
_DEAD = {"EXITED", "TERMINATED", "STOPPED"}


async def _run() -> int:
    config = get_runpod_config()
    client = RunpodClient(config=config)
    try:
        pods = await client.list_pods()
    finally:
        try:
            await client.aclose()
        except Exception:  # noqa: BLE001
            pass

    print("=" * 72)
    print(f"Leaked-pod sweep — {len(pods)} pod(s) on account (read-only)")
    print("=" * 72)

    running, dead, other = [], [], []
    for p in pods:
        status = (p.desired_status or "UNKNOWN").upper()
        (running if status in _RUNNING else dead if status in _DEAD else other).append(p)

    def _line(p) -> str:
        rate = f"${p.cost_per_hr:.3f}/hr" if p.cost_per_hr else "?/hr"
        return (f"  {p.desired_status or '?':<11} {p.id:<20} {rate:<10} "
                f"gpu={p.gpu_count} {p.name or ''}")

    if running:
        burn = sum(p.cost_per_hr or 0.0 for p in running)
        print(f"\n[!!] RUNNING — leaked COMPUTE, ~${burn:.3f}/hr burning NOW:")
        for p in running:
            print(_line(p))
    if dead:
        print(f"\n[ * ] Non-running retained — leaked STORAGE (container disk billed):")
        for p in dead:
            print(_line(p))
    if other:
        print(f"\n[ ? ] Other/unknown status:")
        for p in other:
            print(_line(p))

    print("\n" + "-" * 72)
    print(f"SUMMARY: {len(running)} running, {len(dead)} retained-dead, "
          f"{len(other)} other.")
    if not pods:
        print("CLEAN: no pods on the account.")
    elif not running:
        print("OK: no compute billing (nothing RUNNING).")
    else:
        print("ACTION: terminate the RUNNING pods above to stop the bleed.")
    print("-" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
