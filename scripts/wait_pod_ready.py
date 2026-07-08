"""Poll a pod's runtime.ports until populated. Print SSH info on success."""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.block_m2_video.runpod.runpod_client import RunpodClient

POD_ID = sys.argv[1] if len(sys.argv) > 1 else "ryq3phlu81yhyy"
MAX_WAIT_SEC = int(sys.argv[2]) if len(sys.argv) > 2 else 240


async def main() -> int:
    deadline = time.time() + MAX_WAIT_SEC
    async with RunpodClient() as c:
        while time.time() < deadline:
            pod = await c.get_pod(POD_ID)
            if pod is None:
                print("POD GONE")
                return 1
            uptime = (pod.runtime or {}).get("uptimeInSeconds") if pod.runtime else None
            ports = (pod.runtime or {}).get("ports") if pod.runtime else None
            print(f"[t+{int(time.time() - (deadline - MAX_WAIT_SEC))}s] desired={pod.desired_status} uptime={uptime} ports={'YES' if ports else 'no'}")
            if ports:
                print("PORTS:", json.dumps(ports, indent=2))
                ssh = next((p for p in ports if p.get("privatePort") == 22), None)
                if ssh:
                    print(f"\nSSH READY:")
                    print(f"  ssh root@{ssh['ip']} -p {ssh['publicPort']} -i ~/.ssh/id_ed25519")
                return 0
            await asyncio.sleep(10)
    print(f"TIMEOUT after {MAX_WAIT_SEC}s")
    return 2


sys.exit(asyncio.run(main()))
