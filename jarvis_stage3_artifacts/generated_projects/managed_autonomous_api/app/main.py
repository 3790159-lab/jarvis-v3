from app.api.autonomous_decision import router as autonomous_decision_router
from app.api.hitl_approvals import router as hitl_router
from app.api.mission_memory_v2 import router as mission_memory_v2_router
from app.api.runtime_routes import router as runtime_routes_router
from app.api.feedback import router as feedback_router
from app.api.tools import router as tools_router
from app.api.execution import router as execution_router
from app.api.autonomy import router as autonomy_router
from app.api.bootstrap import router as bootstrap_router
from app.api.maintenance import router as maintenance_router
from app.api.observability import router as observability_router
from app.api.memory import router as memory_router
from app.api.reliability import router as reliability_router
from app.api.routing import router as routing_router
from app.api.agents import router as agents_router
import time
import traceback
from fastapi import FastAPI

app = FastAPI()
app.include_router(autonomous_decision_router)
app.include_router(hitl_router)
app.include_router(mission_memory_v2_router)
app.include_router(runtime_routes_router)
app.include_router(feedback_router)
app.include_router(tools_router)

app.include_router(execution_router)

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

app.include_router(agents_router)

app.include_router(routing_router)

app.include_router(reliability_router)

app.include_router(memory_router)
app.include_router(observability_router)

app.include_router(maintenance_router)

app.include_router(bootstrap_router)

app.include_router(autonomy_router)









