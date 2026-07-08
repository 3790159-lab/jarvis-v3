"""Monitor pod uptime + /system_stats every 5s for N iterations.
Distinguishes 'slow startup' from 'restart loop' from a stuck container."""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from app.services.block_m2_video.runpod.runpod_client import RunpodClient

POD_ID = sys.argv[1] if len(sys.argv) > 1 else "ryq3phlu81yhyy"
ITERATIONS = int(sys.argv[2]) if len(sys.argv) > 2 else 30
INTERVAL = 5

URL = f"https://{POD_ID}-8188.proxy.runpod.net/system_stats"


async def main() -> None:
    async with RunpodClient() as rp, httpx.AsyncClient(timeout=5.0) as http:
        for i in range(ITERATIONS):
            try:
                pod = await rp.get_pod(POD_ID)
                up = (pod.runtime or {}).get("uptimeInSeconds") if pod and pod.runtime else None
                ports = bool((pod.runtime or {}).get("ports") if pod and pod.runtime else None)
            except Exception as e:
                up, ports = f"ERR:{e}", False
            try:
                r = await http.get(URL)
                http_status = r.status_code
            except Exception as e:
                http_status = f"ERR:{type(e).__name__}"
            ts = time.strftime("%H:%M:%S")
            print(f"[{ts}] uptime={up} ports={'Y' if ports else 'n'} http={http_status}")
            await asyncio.sleep(INTERVAL)


asyncio.run(main())
