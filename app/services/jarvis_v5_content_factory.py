from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"
ARTIFACTS = PROJECT_ROOT / "jarvis_stage3_artifacts" / "jarvis_v5_content_factory"
ARTIFACTS.mkdir(parents=True, exist_ok=True)

SCOPES = ["https://www.googleapis.com/auth/drive"]


def _env(name: str, default: str = "") -> str:
    if name in os.environ:
        return os.environ.get(name, default)

    if ENV_PATH.exists():
        text = ENV_PATH.read_text(encoding="utf-8", errors="replace")
        m = re.search(rf"^{re.escape(name)}=(.+)$", text, re.MULTILINE)
        if m:
            return m.group(1).strip().strip('"').strip("'")

    return default


class JarvisV5ContentFactory:
    def __init__(self) -> None:
        self.influencer_key = _env("INFLUENCER_API_KEY")
        self.influencer_base = _env("INFLUENCER_BASE_URL", "https://influencerstudio.com/api/v1").rstrip("/")
        self.workspace_id = _env("INFLUENCER_WORKSPACE_ID")
        self.influencer_id = _env("INFLUENCER_ID")
        self.google_token = PROJECT_ROOT / "google_oauth_token_drive.json"

    def health(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "service": "jarvis_v5_content_factory",
            "influencer_api_key": bool(self.influencer_key),
            "workspace_id": bool(self.workspace_id),
            "influencer_id": bool(self.influencer_id),
            "google_drive_token": self.google_token.exists(),
            "modes": ["luxury_safe", "sensual_fashion_safe"],
            "reserved_future_modes": ["external_adult_provider_if_legal_and_configured"],
        }

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.influencer_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _get(self, path: str) -> Dict[str, Any]:
        r = requests.get(self.influencer_base + path, headers=self._headers(), timeout=90)
        if r.status_code >= 400:
            raise RuntimeError(f"GET {path} failed {r.status_code}: {r.text[:2000]}")
        return r.json()

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        r = requests.post(self.influencer_base + path, headers=self._headers(), json=payload, timeout=90)
        if r.status_code >= 400:
            raise RuntimeError(f"POST {path} failed {r.status_code}: {r.text[:2000]}")
        return r.json()

    def _poll(self, generation_id: str, max_polls: int = 120, delay: int = 5) -> Dict[str, Any]:
        final: Optional[Dict[str, Any]] = None

        for _ in range(max_polls):
            status = self._get(f"/generations/{generation_id}/status")
            if status.get("status") in ["completed", "failed"]:
                final = status
                break
            time.sleep(delay)

        if not final:
            raise TimeoutError(f"Generation timeout: {generation_id}")

        return final

    def _drive(self):
        if not self.google_token.exists():
            raise RuntimeError(f"Google token not found: {self.google_token}")

        creds = Credentials.from_authorized_user_file(str(self.google_token), SCOPES)
        return build("drive", "v3", credentials=creds)

    def _create_drive_folder(self, drive, name: str) -> Dict[str, Any]:
        folder = drive.files().create(
            body={"name": name, "mimeType": "application/vnd.google-apps.folder"},
            fields="id, webViewLink",
        ).execute()

        try:
            drive.permissions().create(
                fileId=folder["id"],
                body={"type": "anyone", "role": "reader"},
                fields="id",
            ).execute()
        except Exception:
            pass

        return drive.files().get(fileId=folder["id"], fields="id, webViewLink").execute()

    def _upload(self, drive, folder_id: str, path: Path, mime: str) -> Dict[str, Any]:
        return drive.files().create(
            body={"name": path.name, "parents": [folder_id]},
            media_body=MediaFileUpload(str(path), mimetype=mime, resumable=False),
            fields="id, name, webViewLink",
        ).execute()

    def _download(self, url: str, path: Path) -> Path:
        r = requests.get(url, timeout=240)
        r.raise_for_status()
        path.write_bytes(r.content)
        return path

    def _prompt_pack(self, style_mode: str, user_prompt: str) -> Dict[str, str]:
        base_rules = (
            "Same trained influencer. Fully adult fictional model. "
            "No nudity. No explicit sexual content. No real-person imitation. "
            "Keep face identity stable, natural skin texture, realistic proportions."
        )

        if style_mode == "sensual_fashion_safe":
            image_prompt = (
                f"{user_prompt}. Premium sensual fashion photoshoot, fully dressed elegant fitted luxury outfit, "
                "stylish hotel suite, mirror, soft cinematic light, confident elegant posture, "
                "photorealistic, 85mm lens, shallow depth of field. "
                + base_rules
            )
            video_prompt = (
                "Premium sensual fashion Instagram reel. Same woman remains fully dressed. "
                "Subtle hip shift, slight shoulder turn, natural smile, gentle hair movement, "
                "slow camera push-in, tasteful luxury style, no exaggerated motion, no body deformation. "
                + base_rules
            )
        else:
            image_prompt = (
                f"{user_prompt}. Ultra realistic luxury Instagram lifestyle photoshoot, elegant brunette influencer, "
                "luxury hotel balcony, ocean view, golden hour, cinematic editorial photography, "
                "85mm lens, natural skin texture, realistic eyes. "
                + base_rules
            )
            video_prompt = (
                "Luxury Instagram reel from reference image. Same woman on luxury hotel balcony, "
                "minimal natural movement, slight breathing, small head turn, gentle hair movement, "
                "slow cinematic push-in, golden hour, ocean background. "
                + base_rules
            )

        return {"image_prompt": image_prompt, "video_prompt": video_prompt}

    def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.influencer_key:
            raise RuntimeError("INFLUENCER_API_KEY missing")
        if not self.workspace_id:
            raise RuntimeError("INFLUENCER_WORKSPACE_ID missing")
        if not self.influencer_id:
            raise RuntimeError("INFLUENCER_ID missing")

        run_id = time.strftime("%Y%m%d_%H%M%S")
        run_dir = ARTIFACTS / f"run_{run_id}"
        run_dir.mkdir(parents=True, exist_ok=True)

        user_prompt = payload.get("prompt") or payload.get("text_prompt") or "luxury lifestyle Instagram content"
        style_mode = payload.get("style_mode") or "luxury_safe"
        image_batch = int(payload.get("image_batch") or 4)
        image_batch = max(1, min(4, image_batch))
        video_enabled = bool(payload.get("video_enabled", True))
        video_model = payload.get("video_model") or "kling-3"

        prompts = self._prompt_pack(style_mode, user_prompt)

        credits_before = self._get("/billing/credits")

        image_payload = {
            "influencer_id": self.influencer_id,
            "workspace_id": self.workspace_id,
            "prompt": prompts["image_prompt"],
            "camera_style": "pro",
            "batch": image_batch,
            "settings": {"aspect_ratio": "9:16"},
        }

        image_created = self._post("/influencers/generate", image_payload)
        image_gid = image_created.get("generation_id")
        if not image_gid:
            raise RuntimeError(f"No image generation_id: {image_created}")

        image_final = self._poll(image_gid)
        image_urls = image_final.get("result_urls", []) or [
            x.get("url") for x in image_final.get("items", []) if x.get("url")
        ]

        local_images: List[Path] = []
        for i, url in enumerate(image_urls, 1):
            path = run_dir / f"photo_{i:03d}.png"
            self._download(url, path)
            local_images.append(path)

        video_final: Optional[Dict[str, Any]] = None
        video_urls: List[str] = []
        local_videos: List[Path] = []

        if video_enabled and image_urls:
            video_payload = {
                "prompt": prompts["video_prompt"],
                "model": video_model,
                "first_frame_image": image_urls[0],
                "settings": {"aspect_ratio": "9:16", "duration": 5},
                "workspace_id": self.workspace_id,
            }

            video_created = self._post("/videos/generate", video_payload)
            video_gid = video_created.get("generation_id")
            if video_gid:
                video_final = self._poll(video_gid)
                video_urls = video_final.get("result_urls", []) or [
                    x.get("url") for x in video_final.get("items", []) if x.get("url")
                ]

                for i, url in enumerate(video_urls, 1):
                    if not url:
                        continue
                    path = run_dir / f"video_{i:03d}.mp4"
                    self._download(url, path)
                    local_videos.append(path)

        drive = self._drive()
        folder = self._create_drive_folder(drive, f"Jarvis_V5_Content_Factory_{run_id}")
        folder_id = folder["id"]

        uploaded: List[Dict[str, Any]] = []

        for img in local_images:
            uploaded.append(self._upload(drive, folder_id, img, "image/png"))

        for vid in local_videos:
            uploaded.append(self._upload(drive, folder_id, vid, "video/mp4"))

        summary = {
            "ok": True,
            "pipeline": "jarvis_v5_super_hybrid",
            "run_id": run_id,
            "style_mode": style_mode,
            "prompt": user_prompt,
            "image_generation_id": image_gid,
            "image_urls": image_urls,
            "video_urls": video_urls,
            "credits_before": credits_before,
            "drive_folder_url": folder.get("webViewLink"),
            "uploaded": uploaded,
            "local_run_dir": str(run_dir),
        }

        summary_path = run_dir / "summary.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        uploaded.append(self._upload(drive, folder_id, summary_path, "application/json"))
        summary["uploaded"] = uploaded

        return summary