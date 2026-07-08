# -*- coding: utf-8 -*-
"""Poll the caught pod's ComfyUI /history for the occlusion-swap prompt until it
finishes, then download the resulting mp4 locally. The headless runner's 1800s
client poll timed out, but ComfyUI keeps rendering on the pod — this grabs the
artifact once node 5 (SAM occlusion) + node 4 (encode) complete.
"""
from __future__ import annotations
import sys, time
from pathlib import Path
import httpx

_BASE = "https://ttytd1z1m99mib-8188.proxy.runpod.net"
_PID = "14ceb08a-883c-4d9b-9982-b68dcb096470"
_OUT = Path("data/block_m2_face_swap/video_outputs")
_DEADLINE_SEC = 1500  # 25 min more


def main() -> int:
    _OUT.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + _DEADLINE_SEC
    with httpx.Client(timeout=90) as c:
        while time.time() < deadline:
            try:
                h = c.get(f"{_BASE}/history/{_PID}").json()
            except Exception as e:
                print(f"[poll] history err: {e}", flush=True); time.sleep(20); continue
            if _PID in h:
                e = h[_PID]
                st = e.get("status") or {}
                print(f"[done] status_str={st.get('status_str')} completed={st.get('completed')}", flush=True)
                msgs = st.get("messages") or []
                errs = [m for m in msgs if "error" in str(m[0]).lower()]
                if errs:
                    print(f"[done] ERROR messages: {errs[:3]}", flush=True)
                for nid, od in (e.get("outputs") or {}).items():
                    for k, items in (od or {}).items():
                        for it in (items or []):
                            fn = it.get("filename", "") if isinstance(it, dict) else ""
                            if str(fn).endswith(".mp4"):
                                r = c.get(f"{_BASE}/view", params={
                                    "filename": fn, "subfolder": it.get("subfolder", ""),
                                    "type": it.get("type", "output")})
                                dest = _OUT / f"occlusion_b53_{fn}"
                                dest.write_bytes(r.content)
                                print(f"[OK] DOWNLOADED {fn} -> {dest} ({len(r.content)} bytes)", flush=True)
                                return 0
                print(f"[done] completed but no mp4. outputs keys={list((e.get('outputs') or {}).keys())}", flush=True)
                return 1
            try:
                q = c.get(f"{_BASE}/queue").json()
                run = len(q.get("queue_running") or [])
            except Exception:
                run = -1
            print(f"[poll] not in history yet (queue_running={run}); sleep 20s", flush=True)
            time.sleep(20)
    print("[timeout] prompt did not complete within fetch deadline", flush=True)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
