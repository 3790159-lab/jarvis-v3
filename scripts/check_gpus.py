"""
Quick check: which GPUs suitable for Wan 2.2 are currently available in our datacenter.
Suitable = 48+ GB VRAM. Run this anytime, free, no Pod created.
"""
import sys, os
sys.path.insert(0, r"C:\jarvis")
os.chdir(r"C:\jarvis")

import asyncio
from app.services.block_m2_video.runpod.runpod_client import RunpodClient
from app.services.block_m2_video.runpod.runpod_config import get_runpod_config

# Cards that work for Wan 2.2 14B i2v
TARGETS = {
    "ideal": [
        ("H200 NVL",        143),
        ("PRO 6000 Max-Q",   96),
        ("PRO 6000 Workstation Edition", 96),
        ("A100 80GB",        80),
        ("A100-SXM4-80GB",   80),
        ("H100 NVL",         94),
        ("H100 PCIe",        80),
        ("H100 80GB",        80),
    ],
    "ok_with_offload": [
        ("RTX A6000",        48),
        ("A40",              48),
        ("RTX 6000 Ada",     48),
        ("L40 ",             48),
        ("L40S",             48),
        ("PRO 6000 Blackwell Server Edition MIG 2g.48gb", 48),
    ],
}

async def main():
    cfg = get_runpod_config()
    print(f"Datacenter: {cfg.datacenter} | Volume: {cfg.network_volume_id}")
    print("Checking GPU availability via probe (no Pod created)...")
    print()

    async with RunpodClient(cfg) as c:
        gpus = await c.list_gpu_types()

        # Index by id substring
        all_gpus = {}
        for g in gpus:
            gid = getattr(g, "id", None) or getattr(g, "displayName", None)
            all_gpus[gid] = g

        print("=" * 72)
        print("IDEAL (80+ GB VRAM, fast generation ~16 min):")
        print("=" * 72)
        for name, vram in TARGETS["ideal"]:
            matches = [gid for gid in all_gpus if name in gid]
            if matches:
                for gid in matches:
                    g = all_gpus[gid]
                    price = getattr(g, "community_price", None) or getattr(g, "communityPrice", None) or "?"
                    print(f"  AVAILABLE  | {vram} GB | ${price:>6}/hr | {gid}")
            else:
                print(f"  not found  | {vram} GB | (no match for '{name}')")

        print()
        print("=" * 72)
        print("OK with offload (48 GB VRAM, ~25-30 min generation):")
        print("=" * 72)
        for name, vram in TARGETS["ok_with_offload"]:
            matches = [gid for gid in all_gpus if name in gid]
            if matches:
                for gid in matches:
                    g = all_gpus[gid]
                    price = getattr(g, "community_price", None) or getattr(g, "communityPrice", None) or "?"
                    print(f"  AVAILABLE  | {vram} GB | ${price:>6}/hr | {gid}")
            else:
                print(f"  not found  | {vram} GB | (no match for '{name}')")

        print()
        print("Note: 'AVAILABLE' here = GPU TYPE exists in catalog.")
        print("Actual instances in our datacenter may still be sold out.")
        print("To check real availability, run: python scripts/runpod_inventory.py")
        print("(it will try each card and show SUPPLY_CONSTRAINT for unavailable ones)")

asyncio.run(main())
