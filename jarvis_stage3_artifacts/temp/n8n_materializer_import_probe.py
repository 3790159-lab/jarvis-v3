import sys
import traceback

project_root = r"C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram"
if project_root not in sys.path:
    sys.path.insert(0, project_root)

modules = [
    "app.services.n8n_workflow_materializer",
    "app.services.n8n_action_router_materializer",
    "app.routers.n8n_action_router_materializer_router",
    "app.main",
]

for name in modules:
    try:
        __import__(name)
        print(f"OK {name}")
    except Exception as exc:
        print(f"FAIL {name}: {exc.__class__.__name__}: {exc}")
        traceback.print_exc()
        raise

import app.main as m
paths = sorted({getattr(r, "path", None) for r in m.app.routes if getattr(r, "path", None)})
print("HAS_ROUTE", "/api/n8n/materializer/health" in paths)

if "/api/n8n/materializer/health" not in paths:
    raise RuntimeError("Route /api/n8n/materializer/health is still missing in imported app")