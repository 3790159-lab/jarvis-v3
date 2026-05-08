from app.main import app

print("=== TOOL ROUTES ===")
for route in app.routes:
    path = getattr(route, "path", None)
    methods = sorted(list(getattr(route, "methods", []) or []))
    if path and path.startswith("/api/tools"):
        print(f"{methods} {path}")
