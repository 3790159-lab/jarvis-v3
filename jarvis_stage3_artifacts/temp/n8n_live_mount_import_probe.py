import json
import sys
import traceback

project_root = r"C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram"
if project_root not in sys.path:
    sys.path.insert(0, project_root)

summary = {}

try:
    from app.services.n8n_workflow_materializer import get_n8n_workflow_materializer
    obj = get_n8n_workflow_materializer()
    summary["compat_getter_ok"] = True
    summary["compat_getter_type"] = obj.__class__.__name__
except Exception as exc:
    summary["compat_getter_ok"] = False
    summary["compat_getter_error"] = f"{exc.__class__.__name__}: {exc}"
    traceback.print_exc()
    raise

modules = [
    "app.services.n8n_workflow_materializer",
    "app.services.n8n_action_router_materializer",
    "app.routers.n8n_action_router_materializer_router",
    "app.main",
]

loaded = []
for name in modules:
    try:
        __import__(name)
        loaded.append(name)
    except Exception as exc:
        print(f"IMPORT_FAIL {name}: {exc.__class__.__name__}: {exc}")
        traceback.print_exc()
        raise

import app.main as m

paths = sorted(
    {
        getattr(route, "path", None)
        for route in m.app.routes
        if getattr(route, "path", None)
    }
)

summary["loaded_modules"] = loaded
summary["has_materializer_health"] = "/api/n8n/materializer/health" in paths
summary["n8n_routes"] = [p for p in paths if "n8n" in p.lower()]
summary["total_routes"] = len(paths)

print(json.dumps(summary, ensure_ascii=False, indent=2))

if not summary["has_materializer_health"]:
    raise RuntimeError("Route /api/n8n/materializer/health missing after import")