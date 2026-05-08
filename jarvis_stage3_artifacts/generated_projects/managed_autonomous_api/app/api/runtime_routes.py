from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/runtime", tags=["runtime"])


@router.get("/routes")
def runtime_routes(request: Request):
    items = []
    for route in request.app.routes:
        path = getattr(route, "path", None)
        methods = sorted(list(getattr(route, "methods", []) or []))
        if path:
            items.append({
                "path": path,
                "methods": methods,
            })
    items.sort(key=lambda x: x["path"])
    return {
        "count": len(items),
        "items": items,
    }
