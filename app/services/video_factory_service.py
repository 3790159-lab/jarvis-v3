from __future__ import annotations

import copy
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from app.services.comfyui_client import ComfyUIClient
from app.prompts.video_prompt_builder import default_negative_prompt


PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = PROJECT_ROOT / "state" / "video_factory"
WORKFLOW_DIR = STATE_DIR / "workflows"
INPUT_DIR = STATE_DIR / "inputs"
OUTPUT_DIR = STATE_DIR / "outputs"
LOG_DIR = STATE_DIR / "logs"

DEFAULT_WORKFLOW = WORKFLOW_DIR / "Wan2.2-Remix-I2V.json"


class VideoFactoryService:
    def __init__(self, comfy_url: Optional[str] = None):
        self.comfy_url = comfy_url or os.getenv("COMFYUI_URL", "http://127.0.0.1:8188")
        self.client = ComfyUIClient(self.comfy_url)

        for path in (STATE_DIR, WORKFLOW_DIR, INPUT_DIR, OUTPUT_DIR, LOG_DIR):
            path.mkdir(parents=True, exist_ok=True)

    def health(self) -> Dict[str, Any]:
        workflow_exists = DEFAULT_WORKFLOW.exists()
        comfy = self.client.health()
        return {
            "ok": bool(workflow_exists and comfy.get("ok")),
            "workflow_exists": workflow_exists,
            "workflow_path": str(DEFAULT_WORKFLOW),
            "comfy_url": self.comfy_url,
            "comfy": comfy,
        }

    def _load_workflow(self, workflow_path: Optional[str] = None) -> Dict[str, Any]:
        path = Path(workflow_path) if workflow_path else DEFAULT_WORKFLOW
        if not path.exists():
            raise FileNotFoundError(f"Workflow not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    def _patch_workflow(
        self,
        workflow: Dict[str, Any],
        image_filename: str,
        positive_prompt: str,
        negative_prompt: Optional[str],
        width: int,
        height: int,
        frames: int,
        fps: int,
        steps: int,
    ) -> Dict[str, Any]:
        wf = copy.deepcopy(workflow)
        negative_prompt = negative_prompt or default_negative_prompt()

        # This workflow is ComfyUI UI graph format. We patch known nodes from uploaded Wan2.2 workflow:
        # 255 = LoadImage, 6 = Positive CLIP, 7 = Negative CLIP,
        # 131 = Width, 130 = Height, 132 = Frames, 68 = Steps,
        # 226/254 = VideoCombine.
        for node in wf.get("nodes", []):
            node_id = node.get("id")
            node_type = node.get("type")
            title = node.get("title", "")

            if node_id == 255 or node_type == "LoadImage":
                node["widgets_values"] = [image_filename, "image"]

            if node_id == 6 or title == "CLIP Text Encode (Positive Prompt)":
                node["widgets_values"] = [positive_prompt]

            if node_id == 7 or title == "CLIP Text Encode (Negative Prompt)":
                node["widgets_values"] = [negative_prompt]

            if node_id == 131 or title == "Width":
                node["widgets_values"] = [int(width)]

            if node_id == 130 or title == "Height":
                node["widgets_values"] = [int(height)]

            if node_id == 132 or title == "Frames":
                node["widgets_values"] = [int(frames)]

            if node_id == 68 or title == "Steps":
                node["widgets_values"] = [int(steps)]

            if node_type == "VHS_VideoCombine":
                wv = node.get("widgets_values")
                if isinstance(wv, dict):
                    wv["frame_rate"] = int(fps)
                    wv["filename_prefix"] = "%date:yyyyMMdd_hhmmss%_Jarvis_Wan22_Remix"
                    wv["save_output"] = True

        return wf

    def run_i2v(
        self,
        image_path: str,
        positive_prompt: str,
        negative_prompt: Optional[str] = None,
        width: int = 560,
        height: int = 720,
        frames: int = 121,
        fps: int = 30,
        steps: int = 8,
        wait: bool = True,
        timeout_sec: int = 3600,
    ) -> Dict[str, Any]:
        src = Path(image_path)
        if not src.exists():
            raise FileNotFoundError(f"Input image not found: {src}")

        job_id = f"vf_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        job_input_dir = INPUT_DIR / job_id
        job_output_dir = OUTPUT_DIR / job_id
        job_input_dir.mkdir(parents=True, exist_ok=True)
        job_output_dir.mkdir(parents=True, exist_ok=True)

        image_filename = src.name
        copied_image = job_input_dir / image_filename
        shutil.copy2(src, copied_image)

        # ComfyUI LoadImage normally reads from ComfyUI/input.
        # We also copy into ComfyUI input if COMFYUI_INPUT_DIR is configured.
        comfy_input_dir = os.getenv("COMFYUI_INPUT_DIR")
        if comfy_input_dir:
            Path(comfy_input_dir).mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, Path(comfy_input_dir) / image_filename)

        workflow = self._load_workflow()
        patched = self._patch_workflow(
            workflow=workflow,
            image_filename=image_filename,
            positive_prompt=positive_prompt,
            negative_prompt=negative_prompt,
            width=width,
            height=height,
            frames=frames,
            fps=fps,
            steps=steps,
        )

        patched_path = job_output_dir / "workflow_patched.json"
        patched_path.write_text(json.dumps(patched, ensure_ascii=False, indent=2), encoding="utf-8")

        queue = self.client.queue_prompt(patched)
        prompt_id = queue.get("prompt_id")

        result: Dict[str, Any] = {
            "ok": True,
            "job_id": job_id,
            "prompt_id": prompt_id,
            "queued": queue,
            "patched_workflow": str(patched_path),
            "output_dir": str(job_output_dir),
        }

        if not wait:
            return result

        history_item = self.client.wait_for_result(prompt_id, timeout_sec=timeout_sec)
        videos = self.client.find_output_videos(history_item)

        downloaded = []
        for v in videos:
            filename = v.get("filename")
            if not filename:
                continue
            subfolder = v.get("subfolder", "")
            file_type = v.get("type", "output")
            out_path = job_output_dir / filename
            try:
                self.client.download_view_file(filename, subfolder, file_type, out_path)
                downloaded.append(str(out_path))
            except Exception as e:
                downloaded.append({"filename": filename, "download_error": str(e)})

        result["history"] = history_item
        result["videos"] = videos
        result["downloaded"] = downloaded

        log_path = LOG_DIR / f"{job_id}.json"
        log_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        result["log_path"] = str(log_path)

        return result