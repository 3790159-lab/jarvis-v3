from app.main import app

print("=== PHASE 14 ROUTES ===")
for route in app.routes:
    path = getattr(route, "path", None)
    methods = sorted(list(getattr(route, "methods", []) or []))
    if path and path.startswith("/api/mission-memory-v2"):
        print(f"{methods} {path}")
