import json
from pathlib import Path

ROOT = Path(r"C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram")
swagger = json.loads((ROOT / "artifacts" / "influencer_swagger.json").read_text(encoding="utf-8"))

targets = [
    "/api/v1/influencers/create",
    "/api/v1/influencers/generate",
    "/api/v1/images/generate-face",
    "/api/v1/images/generate",
    "/api/v1/videos/generate",
    "/api/v1/videos/reference-to-video",
    "/api/v1/videos/talking-head",
    "/api/v1/videos/lipsync",
    "/api/v1/generations/{id}/status",
    "/api/v1/billing/credits",
]

def resolve_ref(obj):
    if isinstance(obj, dict) and "$ref" in obj:
        ref = obj["$ref"]
        parts = ref.lstrip("#/").split("/")
        cur = swagger
        for p in parts:
            cur = cur[p]
        return resolve_ref(cur)
    if isinstance(obj, dict):
        return {k: resolve_ref(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_ref(v) for v in obj]
    return obj

for path in targets:
    print("\n" + "=" * 80)
    print(path)
    item = swagger.get("paths", {}).get(path, {})
    if not item:
        print("NOT FOUND")
        continue

    for method, meta in item.items():
        print("METHOD:", method.upper())
        print("SUMMARY:", meta.get("summary"))
        print("DESCRIPTION:", meta.get("description"))

        rb = meta.get("requestBody")
        if rb:
            print("\nREQUEST BODY:")
            print(json.dumps(resolve_ref(rb), ensure_ascii=False, indent=2)[:8000])

        params = meta.get("parameters")
        if params:
            print("\nPARAMETERS:")
            print(json.dumps(resolve_ref(params), ensure_ascii=False, indent=2)[:4000])

        responses = meta.get("responses")
        if responses:
            print("\nRESPONSES:")
            print(json.dumps(resolve_ref(responses), ensure_ascii=False, indent=2)[:4000])