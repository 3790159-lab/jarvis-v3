from app.main import app

print("=== PHASE 15 ROUTES ===")
for route in app.routes:
    path = getattr(route, "path", None)
    methods = sorted(list(getattr(route, "methods", []) or []))
    if path and path.startswith("/api/hitl"):
        print(f"{methods} {path}")
