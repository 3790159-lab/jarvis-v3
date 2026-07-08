"""Deploy patched bootstrap.sh to network volume via clean idle pod."""
import asyncio, os, sys
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv(Path(__file__).resolve().parent.parent / ".env.runpod")

from app.services.block_m2_video.runpod.runpod_client import RunpodClient
from app.services.block_m2_video.runpod.runpod_config import get_runpod_config

async def main():
    cfg = get_runpod_config()
    # CRITICAL: clear template_id so pod uses raw image without broken startCmd
    cfg.template_id = None
    
    client = RunpodClient(cfg)
    
    print("=== Spawning clean patch pod (no template) ===")
    print(f"Volume: {cfg.network_volume_id}")
    print(f"Datacenter: {cfg.datacenter}")
    
    pod = await client.start_pod(
        name="jarvis-patch-bootstrap",
        image_name="runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04",
        ports="22/tcp",
        container_disk_in_gb=10,
    )
    
    pod_id = pod.id if hasattr(pod, "id") else pod.get("id")
    print(f"\nPod spawned: {pod_id}")
    print("Waiting 90s for SSH ready...")
    await asyncio.sleep(90)
    
    info = await client.get_pod(pod_id)
    runtime = info.runtime if hasattr(info, "runtime") else (info.get("runtime") or {})
    print(f"\n=== Pod info ===")
    print(f"Status: {info.desiredStatus if hasattr(info, 'desiredStatus') else info.get('desiredStatus')}")
    print(f"Runtime: {runtime}")
    print(f"\nPod ID for cleanup: {pod_id}")
    print(f"\nNow open RunPod UI -> pod {pod_id} -> Connect to get SSH command")

if __name__ == "__main__":
    asyncio.run(main())
