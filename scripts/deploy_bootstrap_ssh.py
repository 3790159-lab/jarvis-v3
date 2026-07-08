# -*- coding: utf-8 -*-
"""Reliable bootstrap deploy: raw pod + injected SSH key -> one-shot SSH stdin pipe.

Lesson learned: manual web-terminal paste of a big blob gets chopped by the
terminal (a split mid-paste corrupts the file -> empty /workspace/bootstrap.sh).
podExec is unavailable on this account. A raw pod with NO PUBLIC_KEY had sshd
refusing connections. So:

  1. Spawn a RAW pod (template_id=None, so the currently-EMPTY/broken
     /workspace/bootstrap.sh is NOT executed as startCmd) with the user's
     PUBLIC_KEY injected -> runpod images set up authorized_keys + sshd, root.
  2. Wait for RUNNING, discover the public ip:port mapped to container :22.
  3. SSH in as root (retry while sshd warms up) and stream bootstrap_pod.sh via
     stdin into /workspace/bootstrap.sh; chmod +x; rm -f the root-owned
     .bootstrap_version (root can). Verify NON-EMPTY + version + line count.
  4. ALWAYS stop the pod in finally (no cost leak).

Run: C:\\jarvis\\.venv\\Scripts\\python.exe scripts/deploy_bootstrap_ssh.py
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from app.services.block_m2_video.runpod.runpod_client import (  # noqa: E402
    RunpodClient,
    RunpodSupplyError,
)
from app.services.block_m2_video.runpod.runpod_config import (  # noqa: E402
    get_runpod_config,
)

_BOOTSTRAP_SRC = _ROOT / "scripts" / "remote" / "bootstrap_pod.sh"
_PUBKEY = Path.home() / ".ssh" / "id_ed25519.pub"
_PRIVKEY = Path.home() / ".ssh" / "id_ed25519"
_READY_TIMEOUT_SEC = 180
_POLL_SEC = 10
_SSH_RETRIES = 8
_SSH_RETRY_SLEEP = 15
_SUPPLY_ROUNDS = 80          # ~ rounds of trying the whole list before giving up
_SUPPLY_ROUND_SLEEP = 25     # seconds between rounds when everything is constrained

# ANY GPU works for a file-copy pod — cheapest/most-available first; only a
# successful spawn bills. EU-RO-1 is the volume's datacenter, so the pod must
# land there. We retry the whole list until something frees up.
_DEPLOY_GPU_CANDIDATES = (
    "NVIDIA RTX A4000", "NVIDIA RTX A4500", "NVIDIA RTX A5000",
    "NVIDIA RTX 2000 Ada Generation", "NVIDIA RTX 4000 Ada Generation",
    "NVIDIA L4", "NVIDIA GeForce RTX 3090", "NVIDIA A40", "NVIDIA RTX A6000",
    "NVIDIA A100-SXM4-40GB", "NVIDIA A100 80GB PCIe", "NVIDIA A100-SXM4-80GB",
)

_REMOTE_CMD = (
    "cat > /workspace/bootstrap.sh && "
    "chmod +x /workspace/bootstrap.sh && "
    "rm -f /workspace/.bootstrap_version && "
    "echo '=== DEPLOYED ===' && "
    "id && "
    "grep -m1 BOOTSTRAP_VERSION= /workspace/bootstrap.sh && "
    "wc -l /workspace/bootstrap.sh && "
    "ls -l /workspace/bootstrap.sh && "
    "(test -s /workspace/bootstrap.sh && echo FILE_NONEMPTY || echo FILE_EMPTY) && "
    "(test -f /workspace/.bootstrap_version && echo VER_PRESENT || echo VER_REMOVED)"
)


async def _spawn(client) -> object:
    pubkey = _PUBKEY.read_text(encoding="utf-8").strip()
    for rnd in range(1, _SUPPLY_ROUNDS + 1):
        for gpu in _DEPLOY_GPU_CANDIDATES:
            try:
                pod = await client.start_pod(
                    name="jarvis-deploy-ssh",
                    gpu_type_id=gpu,
                    image_name=client._config.docker_image,
                    ports="22/tcp",
                    container_disk_in_gb=10,
                    env={"PUBLIC_KEY": pubkey},
                )
                print(f"  spawned on '{gpu}': {pod.id} (round {rnd})")
                return pod
            except RunpodSupplyError:
                continue
        print(f"  [round {rnd}/{_SUPPLY_ROUNDS}] all GPUs supply-constrained; "
              f"sleeping {_SUPPLY_ROUND_SLEEP}s")
        await asyncio.sleep(_SUPPLY_ROUND_SLEEP)
    raise RuntimeError("no deploy GPU became available within the retry budget")


def _find_ssh_endpoint(runtime: dict | None):
    """Return (ip, public_port) mapped to container port 22, or None."""
    if not runtime:
        return None
    for p in runtime.get("ports") or []:
        if p.get("privatePort") == 22 and p.get("isIpPublic"):
            return p.get("ip"), p.get("publicPort")
    return None


def _ssh_deploy(ip: str, port: int) -> bool:
    # Encode to UTF-8 bytes ourselves: the bootstrap contains non-ASCII (e.g. the
    # '->' arrow U+2192 in comments). Letting subprocess use text mode on Windows
    # encodes stdin via cp1251 and crashes the writer thread -> truncated transfer.
    src = _BOOTSTRAP_SRC.read_text(encoding="utf-8").replace("\r\n", "\n").encode("utf-8")
    ssh = [
        "ssh", "-o", "StrictHostKeyChecking=accept-new", "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=20", "-i", str(_PRIVKEY), "-p", str(port),
        f"root@{ip}", _REMOTE_CMD,
    ]
    for attempt in range(1, _SSH_RETRIES + 1):
        print(f"  ssh attempt {attempt}/{_SSH_RETRIES} -> {ip}:{port}")
        r = subprocess.run(ssh, input=src, capture_output=True)
        out = (r.stdout or b"").decode("utf-8", "replace")
        if r.returncode == 0 and "=== DEPLOYED ===" in out:
            print("---- remote output ----")
            print(out.strip())
            print("-----------------------")
            return "FILE_NONEMPTY" in out and "VER_REMOVED" in out
        err = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        print(f"    not ready ({err[-1] if err else 'rc=' + str(r.returncode)})")
        import time
        time.sleep(_SSH_RETRY_SLEEP)
    print("  SSH deploy failed after retries.")
    return False


async def main() -> int:
    if not _PUBKEY.exists():
        print(f"ERROR: missing {_PUBKEY}")
        return 1
    print(f"Local bootstrap: {_BOOTSTRAP_SRC} "
          f"({_BOOTSTRAP_SRC.stat().st_size} bytes)")

    cfg = get_runpod_config()
    cfg.template_id = None       # raw image: do NOT run the broken empty startCmd
    cfg.gpu_fallback_id = None   # we drive our own cheap-GPU list
    client = RunpodClient(cfg)

    pod_id = None
    async with client:
        print(f"Spawning raw pod with injected SSH key (volume {cfg.network_volume_id})...")
        pod = await _spawn(client)
        pod_id = pod.id
        try:
            waited = 0
            ep = None
            while waited < _READY_TIMEOUT_SEC:
                info = await client.get_pod(pod_id)
                status = (info.desired_status or "").upper() if info else ""
                ep = _find_ssh_endpoint(info.runtime if info else None)
                if status == "RUNNING" and ep:
                    print(f"  RUNNING after {waited}s; ssh endpoint {ep}")
                    break
                await asyncio.sleep(_POLL_SEC)
                waited += _POLL_SEC
            if not ep:
                print("ERROR: no public :22 endpoint found; cannot SSH. Stopping pod.")
                return 2
            ok = _ssh_deploy(ep[0], int(ep[1]))
            print("\n[RESULT] DEPLOY", "OK" if ok else "FAILED (review output above)")
            return 0 if ok else 3
        finally:
            if pod_id:
                stopped = await client.stop_pod(pod_id)
                print(f"Pod {pod_id} stop requested: {stopped}")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
