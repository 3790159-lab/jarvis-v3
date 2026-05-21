# RunPod Template Configuration

This template carries the `imageName` and `startCmd` for every pod the
sniper (`scripts/runpod_gpu_sniper.py`) catches and every pod
`RunpodClient.start_pod()` deploys. The template ID is set in
`.env.runpod` as `RUNPOD_TEMPLATE_ID`. If the template gets deleted in
the RunPod console, recreate it from the values below.

## Settings

| Field | Value |
|---|---|
| Template Name | `jarvis-i2v-comfyui-bootstrap` (or any name; ID is what matters) |
| Container Image | `runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04` (or current `RUNPOD_DOCKER_IMAGE` value) |
| Container Start Command | `bash -lc '/workspace/bootstrap.sh'` |
| Expose HTTP Ports | `8188` |
| Expose TCP Ports | `22` |
| Container Disk | `50` GB (matches `start_pod()` default) |
| Volume Mount Path | `/workspace` |
| Env Variables | _(none — bootstrap.sh self-contains config)_ |

## Why each field

- **Image** — must match `RUNPOD_DOCKER_IMAGE` so `start_pod()` calls
  that DON'T pass a custom `image_name=` still pick up the template's
  image. (Currently no caller passes `image_name=`.)
- **Start Command** — runs the on-volume bootstrap script. The exact
  string is `bash -lc '/workspace/bootstrap.sh'` (note the single
  quotes — they let `-lc` pass the path as one arg).
- **Ports 8188/http** — ComfyUI's bind port. The HTTPS proxy at
  `https://<pod-id>-8188.proxy.runpod.net` is what `pod_provisioner`
  polls.
- **Ports 22/tcp** — kept for emergency manual SSH access during
  bootstrap debugging. Not used by the Python provisioner.
- **Volume mount `/workspace`** — must match `volume_mount_path` in
  `RunpodClient._deploy_pod()`. Required by RunPod whenever a network
  volume is attached.

## Recovery procedure

1. RunPod console → Templates → New Template.
2. Fill the fields above.
3. Save and copy the template ID.
4. Set `RUNPOD_TEMPLATE_ID=<new_id>` in `.env.runpod`.
5. Verify with `python scripts/runpod_gpu_sniper.py --dry-run` (dry-run
   path doesn't actually deploy a pod, but loads config — confirms the
   env var is read).
6. Run a real sniper catch and confirm the bootstrap log appears on the
   volume.

## Related files

- `scripts/remote/bootstrap_pod.sh` — repo source-of-truth for the
  on-volume script.
- `app/services/block_m2_video/runpod/runpod_config.py` —
  `RunpodConfig.template_id` field definition.
- `app/services/block_m2_video/runpod/runpod_client.py:392-393` — where
  `templateId` is passed into `podFindAndDeployOnDemand`.
