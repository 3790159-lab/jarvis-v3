#!/usr/bin/env python3
"""M.1.2.3 — Smoke test: реальный API без GPU, без _submit_training."""
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

import httpx

PERSONA_PHOTOS = [
    "https://replicate.delivery/xezq/BCOYEf4I0b2sT6PbBiNqEHA4FR75SDroNDzhYtWlGeBFH6hWA/tmpixsw63_b.webp",
    "https://replicate.delivery/xezq/UptHtOzXrE6BO1gTrn3Trp5ieQQOogR10oERO6KHr6MoD9QLA/tmpv_65cso9.webp",
    "https://replicate.delivery/xezq/hr5jQ1ch2upaE1z2JlHf9VV7fvGOEHBD2PoXlGjB1JUfO0DtA/tmponbuz_cg.webp",
    "https://replicate.delivery/xezq/PiCkQceqvDQFLC4SV8CCTh4gJbvjfJK4BTZdAFCS3hWrH6hWA/tmpsbap_2n3.webp",
    "https://replicate.delivery/xezq/eda1KYqYnEyof00r9pAAmyLhXycAkTekNniMwkbkiMPvP0DtA/tmpko7zyfnw.webp",
    "https://replicate.delivery/xezq/6fe8RRKowHtvvUwQgmMdMnlf8F2pSDqTmN65kchxnUNNQ0DtA/tmpk0jg53e8.webp",
    "https://replicate.delivery/xezq/sWyK7PLZOb4GAtJlmmeEeWY11nSyJtVLDUMZUHVvLDZSI6hWA/tmpcytm537_.webp",
    "https://replicate.delivery/xezq/8HEecSuSt9VFKSze3aDgzeTC5WerytKUW2YWj41ceOb6DRP0C/tmpemf6nq64.webp",
    "https://replicate.delivery/xezq/RV900cx9S5KKB5mIh4gijVMeBf1dgHfpJVwguqCsMMkaR0DtA/tmpubwkwrir.webp",
    "https://replicate.delivery/xezq/zML8rW8p63oABVN7dJv7PyerpQfeFXqPJapGlgQ16IVwR0DtA/tmphicvnp4g.webp",
    "https://replicate.delivery/xezq/KePf06lgpqs8ep7HNrnrkOHLqf8b5KU61tDd2ioA6nvakoHaB/tmpccgmb6gk.webp",
    "https://replicate.delivery/xezq/UOItZAnRWW6cExwpZ0b3vTv8DHjscX64JB4v7g8pfIOqE9QLA/tmpoukgk_sc.webp",
    "https://replicate.delivery/xezq/36cJrmu6RSoZO5L90inJhTXY1zwivQeBKcoiCaJIFvvwE9QLA/tmpa9nvqgdi.webp",
    "https://replicate.delivery/xezq/zkfeGewDQ1H3AIgaAeQez3YysWSaUAZBzm3yQexvaSa0aieQLA/tmptrp8pu46.webp",
    "https://replicate.delivery/xezq/uJm5NVdFmnKpLlox5pVUV5NETtNhkQ46RXofJU3PqDo7E9QLA/tmplg6kqse6.webp",
    "https://replicate.delivery/xezq/XC9V9a01aAaVMFN8wx24uPUNflt5NWmKedNSfgHeIslpooHaB/tmpjvh4ivf0.webp",
    "https://replicate.delivery/xezq/zgeQdktBYnTwKataMGezIy4rgZ6neeNM2GdRcRByOdyTpoHaB/tmp6v9e20q6.webp",
    "https://replicate.delivery/xezq/GjO8Zp01Pf1iWadd8dJ4EaNXSPRPQlz5erJzzbsp4xWfU0DtA/tmprv8p6mjf.webp",
    "https://replicate.delivery/xezq/bZnWkyEWzPI0D93aFz34RTb3A3vHy4chPP3WIfXxgdYWF9QLA/tmpkg_wklwb.webp",
    "https://replicate.delivery/xezq/oZ6ucwWFovrpHFkuROZMb8rYL6kvpSgk3rPxTZSbWqUuieQLA/tmp5azcwadp.webp",
]

FALLBACK_PHOTOS = [
    "https://picsum.photos/seed/smoke1/512/512",
    "https://picsum.photos/seed/smoke2/512/512",
    "https://picsum.photos/seed/smoke3/512/512",
]

TRIGGER_WORD = "sks_smoke_test"


async def check_url_alive(url: str) -> bool:
    """GET the URL (follow redirects) and verify we get actual image bytes."""
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as s:
            r = await s.get(url)
            return r.status_code == 200 and len(r.content) > 100
    except Exception:
        return False


async def main() -> None:
    from app.services.block_m_common.replicate_video_client import ReplicateVideoClient

    client = ReplicateVideoClient()

    sep = "=" * 64
    print(sep)
    print("  M.1.2.3 Smoke Test — Replicate API (no GPU, no training)")
    print(sep)

    total_t0 = time.perf_counter()

    # ── [1] _get_username ─────────────────────────────────────────────────────
    t0 = time.perf_counter()
    username = await client._get_username()
    dt = time.perf_counter() - t0
    destination = f"{username}/{TRIGGER_WORD.replace('_', '-')}-lora"
    print(f"\n[1] _get_username()")
    print(f"    returned:     {username}")
    print(f"    destination:  {destination}")
    print(f"    took:         {dt:.2f}s")

    # ── [2] _get_latest_version ───────────────────────────────────────────────
    t0 = time.perf_counter()
    version = await client._get_latest_version("ostris/flux-dev-lora-trainer")
    dt = time.perf_counter() - t0
    print(f"\n[2] _get_latest_version('ostris/flux-dev-lora-trainer')")
    print(f"    version hash: {version}")
    print(f"    took:         {dt:.2f}s")

    # ── [3] _ensure_destination_exists ────────────────────────────────────────
    owner, name = destination.split("/", 1)
    pre_check_url = f"https://api.replicate.com/v1/models/{owner}/{name}"
    async with httpx.AsyncClient(timeout=10.0) as s:
        pre = await s.get(pre_check_url, headers={"Authorization": f"Token {client._api_token}"})
        pre_exists = pre.status_code == 200

    t0 = time.perf_counter()
    await client._ensure_destination_exists(destination)
    dt = time.perf_counter() - t0
    status_str = "already_exists" if pre_exists else "created"
    print(f"\n[3] _ensure_destination_exists('{destination}')")
    print(f"    status:       {status_str}")
    print(f"    took:         {dt:.2f}s")

    # ── [4] choose images ─────────────────────────────────────────────────────
    print(f"\n[4] Checking persona_3694ea6b seed photos (20 URLs)...")
    urls_alive = await check_url_alive(PERSONA_PHOTOS[0])
    if urls_alive:
        images = PERSONA_PHOTOS
        source = "persona_3694ea6b (20 seed photos)"
    else:
        images = FALLBACK_PHOTOS
        source = "picsum.photos fallback (3 images)"
    print(f"    source:       {source}")

    # ── [5] _create_zip_from_urls ─────────────────────────────────────────────
    print(f"\n[5] _create_zip_from_urls({len(images)} images)...")
    t0 = time.perf_counter()
    zip_bytes = await client._create_zip_from_urls(images)
    dt = time.perf_counter() - t0
    kb = len(zip_bytes) / 1024
    mb = kb / 1024
    print(f"    bytes_size:   {len(zip_bytes):,} bytes  ({mb:.2f} MB)")
    print(f"    took:         {dt:.2f}s for {len(images)} images  ({kb/len(images):.1f} KB/img avg)")

    # ── [6] _upload_zip_to_replicate ──────────────────────────────────────────
    print(f"\n[6] _upload_zip_to_replicate({mb:.2f} MB)...")
    t0 = time.perf_counter()
    zip_url = await client._upload_zip_to_replicate(zip_bytes)
    dt = time.perf_counter() - t0
    print(f"    returned URL: {zip_url}")
    print(f"    took:         {dt:.2f}s")

    # ── Summary ───────────────────────────────────────────────────────────────
    total_dt = time.perf_counter() - total_t0
    print(f"\n{sep}")
    print("  SUMMARY - vse shagi proshli OK")
    print(sep)
    print(f"  username:      {username}")
    print(f"  destination:   {destination}  [{status_str}]")
    print(f"  model version: {version}")
    print(f"  images:        {len(images)}  ({source.split('(')[0].strip()})")
    print(f"  ZIP size:      {mb:.2f} MB")
    print(f"  ZIP URL:       {zip_url}")
    print(f"  total time:    {total_dt:.1f}s")
    print()
    print("  Готово к обучению. Payload для _submit_training():")
    print(f"  POST .../versions/{version}/trainings")
    print(f"  destination  = {destination}")
    print(f"  input_images = {zip_url}")
    print(f"  trigger_word = {TRIGGER_WORD}")
    print(f"  steps        = 1000")
    print(sep)


if __name__ == "__main__":
    asyncio.run(main())
