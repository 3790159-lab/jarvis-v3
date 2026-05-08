from app.main import app

print("=== FEEDBACK ROUTES ===")
for route in app.routes:
    path = getattr(route, "path", None)
    methods = sorted(list(getattr(route, "methods", []) or []))
    if path and path.startswith("/api/feedback"):
        print(f"{methods} {path}")
