# -*- coding: utf-8 -*-
"""Watch a running ComfyUI prompt to completion, then download the mp4.

Polls /history (done?) + /queue (running?) and takes a short websocket sample
each iteration to report the executing node + frame progress. When the prompt
lands in /history with a video output, downloads it via /view to the local
video_outputs dir and exits. Survives the bot's 30-min poll timeout — the pod
keeps running (FACE_SWAP_KEEP_POD_RUNNING=1), so the file is retrievable even
after the bot gives up.

Usage: python scripts/monitor_swap_until_done.py <prompt_id> [max_minutes]
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.parse

import httpx

BASE = "https://us24ohc20a65ty-8188.proxy.runpod.net"
H = {"User-Agent": "Mozilla/5.0"}
OUTDIR = r"C:\jarvis\data\block_m2_face_swap\video_outputs"


async def ws_sample(seconds: float = 6.0) -> str:
    """Connect briefly, return a one-line summary of the latest progress."""
    try:
        import websockets
    except Exception:
        return "ws:n/a"
    uri = BASE.replace("https", "wss") + "/ws?clientId=monitor"
    last = "ws:no-msg"
    try:
        async with websockets.connect(
            uri, additional_headers=H, open_timeout=15
        ) as ws:
            loop = asyncio.get_event_loop()
            end = loop.time() + seconds
            node = None
            while loop.time() < end:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=seconds)
                except asyncio.TimeoutError:
                    break
                if isinstance(msg, bytes):
                    last = "ws:preview-image-frame"
                    continue
                d = json.loads(msg)
                t = d.get("type")
                dd = d.get("data", {})
                if t in ("progress", "progress_state"):
                    v, m = dd.get("value"), dd.get("max")
                    n = dd.get("node") or node
                    if v is not None:
                        last = f"node {n}: frame {v}/{m}"
                elif t == "executing":
                    node = dd.get("node")
                    last = f"executing node {node}"
    except Exception as exc:
        return f"ws-err:{type(exc).__name__}"
    return last


def fetch_history(pid: str) -> dict:
    try:
        return httpx.get(f"{BASE}/history/{pid}", headers=H, timeout=30).json()
    except Exception:
        return {}


def queue_running() -> int:
    try:
        q = httpx.get(f"{BASE}/queue", headers=H, timeout=20).json()
        return len(q.get("queue_running", []))
    except Exception:
        return -1


def download_output(pid: str, hist: dict) -> str | None:
    outs = hist.get(pid, {}).get("outputs", {})
    for nid, o in outs.items():
        for v in (o.get("gifs", []) + o.get("videos", [])):
            fn = v.get("filename")
            if not fn or not fn.lower().endswith((".mp4", ".webm", ".mkv")):
                continue
            params = urllib.parse.urlencode({
                "filename": fn,
                "subfolder": v.get("subfolder", ""),
                "type": v.get("type", "output"),
            })
            r = httpx.get(f"{BASE}/view?{params}", headers=H, timeout=120)
            if r.status_code == 200 and len(r.content) > 1024:
                os.makedirs(OUTDIR, exist_ok=True)
                dst = os.path.join(OUTDIR, f"occlusion_test_{pid[:8]}_{fn}")
                with open(dst, "wb") as fh:
                    fh.write(r.content)
                return dst
    return None


async def main() -> int:
    pid = sys.argv[1]
    max_min = float(sys.argv[2]) if len(sys.argv) > 2 else 45.0
    loop = asyncio.get_event_loop()
    deadline = loop.time() + max_min * 60
    last_node = None
    i = 0
    while loop.time() < deadline:
        i += 1
        hist = fetch_history(pid)
        if hist.get(pid):
            st = hist[pid].get("status", {})
            print(f"[done] status={st.get('status_str')} "
                  f"completed={st.get('completed')}", flush=True)
            dst = download_output(pid, hist)
            if dst:
                print(f"[saved] {dst} ({os.path.getsize(dst)} bytes)", flush=True)
            else:
                print("[warn] history present but no downloadable video output",
                      flush=True)
            return 0
        run = queue_running()
        prog = await ws_sample(6.0)
        # surface node transitions prominently
        cur_node = prog.split("node")[1].split(":")[0].strip() if "node" in prog else None
        marker = ""
        if cur_node and cur_node != last_node:
            marker = f"  <<< NODE CHANGED -> {cur_node}"
            last_node = cur_node
        mins = (loop.time() - (deadline - max_min * 60)) / 60
        print(f"[t+{mins:4.1f}m] queue_running={run} | {prog}{marker}", flush=True)
        await asyncio.sleep(24)
    print("[timeout] monitor gave up; prompt still not in history", flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
