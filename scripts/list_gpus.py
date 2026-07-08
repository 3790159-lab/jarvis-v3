import sys, os
sys.path.insert(0, r"C:\jarvis")
os.chdir(r"C:\jarvis")

import asyncio
from app.services.block_m2_video.runpod.runpod_client import RunpodClient
from app.services.block_m2_video.runpod.runpod_config import get_runpod_config

async def main():
    cfg = get_runpod_config()
    async with RunpodClient(cfg) as c:
        gpus = await c.list_gpu_types()
        print(f"Total GPU types: {len(gpus)}")
        rows = []
        for g in gpus:
            name = getattr(g, "display_name", None) or getattr(g, "displayName", None) or getattr(g, "id", "?")
            vram = getattr(g, "memory_in_gb", None) or getattr(g, "memoryInGb", "?")
            price = getattr(g, "community_price", None) or getattr(g, "communityPrice", None)
            secure = getattr(g, "secure_price", None) or getattr(g, "securePrice", None)
            vram_int = vram if isinstance(vram, int) else 0
            rows.append((vram_int, price or 999.0, name, vram, price, secure))
        rows.sort(key=lambda r: (-r[0], r[1]))
        for _, _, name, vram, price, secure in rows:
            ps = ("$" + str(price)) if price else "N/A"
            ss = ("$" + str(secure)) if secure else "N/A"
            print(f"  {str(vram):>5} GB | community {ps:>8} | secure {ss:>8} | {name}")

asyncio.run(main())
