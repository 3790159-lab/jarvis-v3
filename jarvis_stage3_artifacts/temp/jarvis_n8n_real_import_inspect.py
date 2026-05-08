from __future__ import annotations

import json
import sys
from pathlib import Path

project_root = Path(r"C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram")
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import app.main as main_mod
import app.services.n8n_workflow_materializer as mat_mod
import app.services.n8n_action_router_materializer as svc_mod
import app.routers.n8n_action_router_materializer_router as router_mod

routes = sorted(
    {
        getattr(route, "path", None)
        for route in main_mod.app.routes
        if getattr(route, "path", None)
    }
)

payload = {
    "main_file": str(Path(main_mod.__file__).resolve()),
    "materializer_file": str(Path(mat_mod.__file__).resolve()),
    "service_file": str(Path(svc_mod.__file__).resolve()),
    "router_file": str(Path(router_mod.__file__).resolve()),
    "n8n_routes": [r for r in routes if r and "n8n" in r.lower()],
}

print(json.dumps(payload, ensure_ascii=False, indent=2))