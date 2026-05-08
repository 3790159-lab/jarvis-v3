from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(r"C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram")
OUT_PATH = PROJECT_ROOT / "jarvis_stage3_artifacts" / "config" / "n8n_sidecar_runtime_config.json"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

def safe_getattr(obj, name, default=None):
    try:
        return getattr(obj, name, default)
    except Exception:
        return default

def extract_from_object(obj, source_name: str):
    base_url = safe_getattr(obj, "base_url")
    webhook_base_url = safe_getattr(obj, "webhook_base_url")
    api_key = safe_getattr(obj, "api_key")
    timeout_seconds = safe_getattr(obj, "timeout_seconds")

    return {
        "source": source_name,
        "base_url": base_url,
        "webhook_base_url": webhook_base_url,
        "api_key": api_key,
        "timeout_seconds": timeout_seconds,
        "has_api_key": bool(api_key),
    }

candidates = []
result = None

# Candidate 1: old materializer getter
try:
    import app.services.n8n_workflow_materializer as mod1
    if hasattr(mod1, "get_n8n_workflow_materializer"):
        obj = mod1.get_n8n_workflow_materializer()
        cand = extract_from_object(obj, f"{mod1.__file__}::get_n8n_workflow_materializer")
        candidates.append(cand)
        if cand["base_url"] and cand["has_api_key"]:
            result = cand
except Exception as exc:
    candidates.append({"source": "app.services.n8n_workflow_materializer", "error": f"{exc.__class__.__name__}: {exc}"})

# Candidate 2: instantiate old class directly
if result is None:
    try:
        import app.services.n8n_workflow_materializer as mod2
        if hasattr(mod2, "N8NWorkflowMaterializer"):
            obj = mod2.N8NWorkflowMaterializer()
            cand = extract_from_object(obj, f"{mod2.__file__}::N8NWorkflowMaterializer()")
            candidates.append(cand)
            if cand["base_url"] and cand["has_api_key"]:
                result = cand
    except Exception as exc:
        candidates.append({"source": "N8NWorkflowMaterializer()", "error": f"{exc.__class__.__name__}: {exc}"})

# Candidate 3: action router -> materializer
if result is None:
    try:
        import app.services.n8n_action_router_materializer as mod3
        if hasattr(mod3, "N8NActionRouterMaterializer"):
            svc = mod3.N8NActionRouterMaterializer()
            obj = safe_getattr(svc, "materializer")
            cand = extract_from_object(obj, f"{mod3.__file__}::N8NActionRouterMaterializer.materializer")
            candidates.append(cand)
            if cand["base_url"] and cand["has_api_key"]:
                result = cand
    except Exception as exc:
        candidates.append({"source": "N8NActionRouterMaterializer.materializer", "error": f"{exc.__class__.__name__}: {exc}"})

# Candidate 4: env fallback
if result is None:
    cand = {
        "source": "os.environ",
        "base_url": os.getenv("N8N_BASE_URL"),
        "webhook_base_url": os.getenv("N8N_WEBHOOK_BASE_URL"),
        "api_key": os.getenv("N8N_API_KEY") or os.getenv("N8N_BEARER_TOKEN"),
        "timeout_seconds": os.getenv("N8N_TIMEOUT_SECONDS") or "45",
        "has_api_key": bool(os.getenv("N8N_API_KEY") or os.getenv("N8N_BEARER_TOKEN")),
    }
    candidates.append(cand)
    if cand["base_url"] and cand["has_api_key"]:
        result = cand

payload = {
    "ok": result is not None,
    "selected": result,
    "candidates": candidates,
}

OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(payload, ensure_ascii=False, indent=2))

if result is None:
    raise SystemExit(2)