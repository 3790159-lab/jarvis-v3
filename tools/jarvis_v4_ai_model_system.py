import os
import json
import requests
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"

env = ENV_PATH.read_text(encoding="utf-8")

def get_env(name):
    import re
    m = re.search(rf"{name}=(.+)", env)
    return m.group(1).strip() if m else ""

API_KEY = get_env("INFLUENCER_API_KEY")
BASE_URL = get_env("INFLUENCER_BASE_URL")

if not API_KEY:
    raise Exception("NO API KEY")

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

print("== CREATE AI MODEL ==")

model_payload = {
    "name": "Elara Voss",
    "style": "luxury cinematic instagram model",
    "age": 23,
    "gender": "female",
    "persona": "premium influencer, travel, lifestyle"
}

r = requests.post(
    f"{BASE_URL}/models",
    headers=HEADERS,
    json=model_payload
)

model = r.json()
model_id = model.get("id")

print("model_id:", model_id)

print("== GENERATE SCENE ==")

scene_payload = {
    "model_id": model_id,
    "scene": "sunset beach luxury dress cinematic lighting",
    "format": "image"
}

img = requests.post(
    f"{BASE_URL}/images",
    headers=HEADERS,
    json=scene_payload
)

print("image_result:", img.json())

print("== GENERATE VIDEO ==")

video_payload = {
    "model_id": model_id,
    "scene": "walking on beach slow motion luxury",
    "duration": 5
}

vid = requests.post(
    f"{BASE_URL}/videos",
    headers=HEADERS,
    json=video_payload
)

print("video_result:", vid.json())

print("DONE")