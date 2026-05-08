from app.main import app

print("=== ROUTES ===")
for route in app.routes:
    path = getattr(route, "path", None)
    methods = sorted(list(getattr(route, "methods", []) or []))
    print(f"{methods} {path}")
