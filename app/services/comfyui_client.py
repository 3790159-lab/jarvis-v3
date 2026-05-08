from __future__ import annotations

import json
import time
import uuid
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Optional


class ComfyUIClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8188"):
        self.base_url = base_url.rstrip("/")
        self.client_id = str(uuid.uuid4())

    def health(self) -> Dict[str, Any]:
        try:
            with urllib.request.urlopen(f"{self.base_url}/system_stats", timeout=5) as r:
                return {"ok": True, "system_stats": json.loads(r.read().decode("utf-8"))}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def queue_prompt(self, workflow: Dict[str, Any]) -> Dict[str, Any]:
        payload = json.dumps({
            "prompt": workflow,
            "client_id": self.client_id,
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{self.base_url}/prompt",
            data=payload,
            headers={"Content-Type": "application/json"},
        )

        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))

    def get_history(self, prompt_id: str) -> Dict[str, Any]:
        with urllib.request.urlopen(f"{self.base_url}/history/{prompt_id}", timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))

    def wait_for_result(self, prompt_id: str, timeout_sec: int = 3600, poll_sec: float = 2.0) -> Dict[str, Any]:
        started = time.time()
        while True:
            history = self.get_history(prompt_id)
            if prompt_id in history:
                return history[prompt_id]

            if time.time() - started > timeout_sec:
                raise TimeoutError(f"ComfyUI job timeout: {prompt_id}")

            time.sleep(poll_sec)

    def find_output_videos(self, history_item: Dict[str, Any]) -> list[Dict[str, Any]]:
        videos: list[Dict[str, Any]] = []

        outputs = history_item.get("outputs", {})
        for node_id, node_output in outputs.items():
            for key in ("videos", "gifs", "files"):
                for item in node_output.get(key, []) or []:
                    videos.append({
                        "node_id": node_id,
                        "kind": key,
                        **item,
                    })

        return videos

    def download_view_file(self, filename: str, subfolder: str = "", file_type: str = "output", out_path: Optional[Path] = None) -> Path:
        params = urllib.parse.urlencode({
            "filename": filename,
            "subfolder": subfolder or "",
            "type": file_type or "output",
        })

        url = f"{self.base_url}/view?{params}"

        if out_path is None:
            out_path = Path(filename)

        out_path.parent.mkdir(parents=True, exist_ok=True)

        with urllib.request.urlopen(url, timeout=120) as r:
            out_path.write_bytes(r.read())

        return out_path