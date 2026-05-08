import time
import traceback
from fastapi import FastAPI

app = FastAPI()

# ----------------------------
# SAFE LIFESPAN (ANTI-CRASH)
# ----------------------------
@app.on_event("startup")
async def startup_event():
    try:
        print("[APP] Startup begin", flush=True)
        # здесь можно добавить init позже
        print("[APP] Startup complete", flush=True)
    except Exception as e:
        print("[APP][ERROR] Startup failed:", str(e), flush=True)
        traceback.print_exc()


@app.on_event("shutdown")
async def shutdown_event():
    try:
        print("[APP] Shutdown triggered", flush=True)
    except Exception:
        pass


# ----------------------------
# HEALTH ENDPOINT (CRITICAL)
# ----------------------------
START_TIME = time.time()

@app.get("/health")
def health():
    return {
        "status": "healthy",
        "service_status": "ok",
        "service": "managed_autonomous_api",
        "version": "3.5.0",
        "pid": __import__("os").getpid(),
        "uptime_seconds": round(time.time() - START_TIME, 2)
    }


# ----------------------------
# ANTI-EXIT LOOP (CRITICAL)
# ----------------------------
@app.get("/")
def root():
    return {"message": "API running"}


# ----------------------------
# GLOBAL EXCEPTION HOOK
# ----------------------------
@app.middleware("http")
async def catch_exceptions(request, call_next):
    try:
        return await call_next(request)
    except Exception as e:
        print("[APP][CRASH]", str(e), flush=True)
        traceback.print_exc()
        raise
