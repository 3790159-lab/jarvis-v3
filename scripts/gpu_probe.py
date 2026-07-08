#!/usr/bin/env python3
"""Query RunPod GPU availability in EU-RO-1 (>=48GB VRAM)."""
import asyncio, sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
from app.services.block_m2_video.runpod.runpod_config import get_runpod_config
import httpx

DC = "EU-RO-1"
MIN_VRAM = 48

Q1 = """query A($dc:String!){gpuTypes(input:{dataCenterId:$dc}){id displayName memoryInGb lowestPrice(input:{gpuCount:1,dataCenterId:$dc}){minimumBidPrice uninterruptablePrice stockStatus}}}"""
Q2 = """query B{gpuTypes{id displayName memoryInGb lowestPrice(input:{gpuCount:1,dataCenterId:"EU-RO-1"}){minimumBidPrice uninterruptablePrice stockStatus}}}"""
Q3 = """query C{gpuTypes{id displayName memoryInGb securePrice communityPrice}}"""


async def gql(client, cfg, q, variables=None):
    body = {"query": q}
    if variables:
        body["variables"] = variables
    r = await client.post(
        cfg.api_endpoint,
        json=body,
        headers={"Authorization": "Bearer " + cfg.api_key.get_secret_value(),
                 "Content-Type": "application/json"},
    )
    return r.json()


def show(gpus):
    for g in sorted(gpus, key=lambda x: -(x.get("memoryInGb") or 0)):
        lp  = g.get("lowestPrice") or {}
        mem = g.get("memoryInGb", "?")
        did = g.get("id", "")
        dn  = g.get("displayName", did)
        stk = lp.get("stockStatus", "N/A")
        pr  = lp.get("uninterruptablePrice") or lp.get("minimumBidPrice")
        sp  = g.get("securePrice")
        cp  = g.get("communityPrice")
        if pr is not None:
            print(f"  [{stk:<14}]  {mem:>3}GB  ${pr:.3f}/hr  {dn}  ||  {did}")
        else:
            print(f"  [no_dc_price  ]  {mem:>3}GB  secure={sp} comm={cp}  {dn}  ||  {did}")


async def _run():
    cfg = get_runpod_config()
    async with httpx.AsyncClient(timeout=30) as client:

        print(f"\\n=== Q1: gpuTypes(dataCenterId={DC}) with stockStatus ===")
        d1 = await gql(client, cfg, Q1, {"dc": DC})
        if "errors" not in d1 and (d1.get("data") or {}).get("gpuTypes") is not None:
            g1 = [g for g in d1["data"]["gpuTypes"] if (g.get("memoryInGb") or 0) >= MIN_VRAM]
            print(f"Q1 OK — {len(g1)} GPU(s) >={MIN_VRAM}GB in {DC}:")
            show(g1)
        else:
            print("Q1 failed:", d1.get("errors", "(no data)"))

        print(f"\\n=== Q2: all gpuTypes, lowestPrice filtered to {DC} ===")
        d2 = await gql(client, cfg, Q2)
        if "errors" not in d2 and (d2.get("data") or {}).get("gpuTypes") is not None:
            g2 = [g for g in d2["data"]["gpuTypes"]
                  if (g.get("memoryInGb") or 0) >= MIN_VRAM
                  and (g.get("lowestPrice") or {}).get("uninterruptablePrice")]
            print(f"Q2 OK — {len(g2)} GPU(s) >={MIN_VRAM}GB with on-demand price in {DC}:")
            show(g2)
        else:
            print("Q2 failed:", d2.get("errors", "(no data)"))

        print(f"\\n=== Q3: flat fallback (no DC filter) ===")
        d3 = await gql(client, cfg, Q3)
        g3 = [g for g in ((d3.get("data") or {}).get("gpuTypes") or [])
              if (g.get("memoryInGb") or 0) >= MIN_VRAM]
        print(f"Q3: {len(g3)} GPU(s) >={MIN_VRAM}GB globally:")
        show(g3)


if __name__ == "__main__":
    asyncio.run(_run())