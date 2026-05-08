from __future__ import annotations
from app.routers.jarvis_v5_content_factory import router as jarvis_v5_content_factory_router
from app.routers.jarvis_live_operator import router as jarvis_live_operator_router
from app.routers import operator_dashboard
from app.routers import time_brain




import importlib
import os
from typing import Any, Dict, List

from fastapi import FastAPI
from app.routers import claude_ecosystem
from app.routers import time_brain
from fastapi.middleware.cors import CORSMiddleware


def _bool_env(name: str) -> bool:
    value = os.getenv(name, "").strip()
    return bool(value)


def _build_ai_health() -> Dict[str, Any]:
    openai_configured = _bool_env("OPENAI_API_KEY") or _bool_env("LLM_API_KEY")
    anthropic_configured = _bool_env("ANTHROPIC_API_KEY")
    ollama_configured = True

    providers: List[Dict[str, Any]] = [
        {
            "provider": "openai",
            "enabled": True,
            "configured": openai_configured,
            "details": {"timeout_seconds": 600},
        },
        {
            "provider": "anthropic",
            "enabled": True,
            "configured": anthropic_configured,
            "details": {"timeout_seconds": 600},
        },
        {
            "provider": "ollama",
            "enabled": True,
            "configured": ollama_configured,
            "details": {"timeout_seconds": 600},
        },
    ]

    default_provider = os.getenv("DEFAULT_PROVIDER", "").strip().lower() or "ollama"

    return {
        "status": "healthy",
        "providers": providers,
        "default_provider": default_provider,
    }


app = FastAPI(title="Jarvis V3 Supervisor", version="3.0.0")
app.include_router(jarvis_v5_content_factory_router)
app.include_router(jarvis_live_operator_router)
app.include_router(claude_ecosystem.router)
app.include_router(time_brain.router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "healthy",
        "service": "jarvis_v3_supervisor",
        "env": os.getenv("APP_ENV", "dev"),
    }


@app.get("/api/ai/health")
def ai_health() -> Dict[str, Any]:
    return _build_ai_health()


def _safe_include(module_name: str, attr_name: str = "router") -> None:
    try:
        module = importlib.import_module(module_name)
        router = getattr(module, attr_name, None)
        if router is not None:
            app.include_router(router)
            print(f"[OK] Included router: {module_name}.{attr_name}")
    except Exception as exc:
        print(f"[WARN] Router skipped: {module_name}.{attr_name} -> {exc}")


ROUTER_CANDIDATES = [
    ("app.api.health", "router"),
    ("app.api.ai_router", "router"),
    ("app.api.routes", "router"),
    ("app.api.goals_router", "router"),
    ("app.api.routes.missions", "router"),
    ("app.api.endpoints.missions", "router"),
    ("app.api.missions", "router"),
    ("app.api.responses", "router"),
    ("app.api.specialized_agents", "router"),
    ("app.api.spreadsheets", "router"),
    ("app.api.google_tools", "router"),
    ("app.api.artifacts", "router"),
    ("app.api.dashboard", "router"),
    ("app.api.memory", "router"),
    ("app.api.memory_layer", "router"),
    ("app.api.mission_ai_bridge", "router"),
    ("app.api.mission_artifacts", "router"),
    ("app.api.mission_graph", "router"),
    ("app.api.mission_memory_bridge", "router"),
    ("app.api.mission_run_memory", "router"),
    ("app.api.multi_step_execution", "router"),
    ("app.api.obsidian_bridge", "router"),
    ("app.api.policy", "router"),
    ("app.api.provider_routing_policy", "router"),
    ("app.api.runtime_bridge", "router"),
    ("app.api.external_executor", "router"),
    ("app.api.worker", "router"),
    ("app.api.cloud_control", "router"),
    ("app.api.compat_legacy", "router"),
    ("app.api.execution_planner", "router"),

    ("app.autonomy.router", "router"),
    ("app.autonomy.mission_bridge", "router"),

    ("app.routers.multistep", "router"),
    ("app.routers.resume_recovery", "router"),
    ("app.routers.tools_runtime", "router"),
    ("app.routers.tool_routing", "router"),
    ("app.routers.tool_chains", "router"),
    ("app.routers.artifacts_runtime", "router"),
    ("app.routers.agent_control_plane", "router"),
]

for module_name, attr_name in ROUTER_CANDIDATES:
    _safe_include(module_name, attr_name)


@app.on_event("startup")
def startup_event() -> None:
    print("[INFO] Jarvis backend startup complete")

try:
    from app.api import agent_mesh_router
    app.include_router(agent_mesh_router.router)
    print("[OK] Included router: app.api.agent_mesh_router.router")
except Exception as e:
    print(f"[WARN] Router skipped: app.api.agent_mesh_router -> {e}")

try:
    from app.api import agent_mesh_plus_router
    app.include_router(agent_mesh_plus_router.router)
    print("[OK] Included router: app.api.agent_mesh_plus_router.router")
except Exception as e:
    print(f"[WARN] Router skipped: app.api.agent_mesh_plus_router -> {e}")

try:
    from app.api import agent_mesh_stability_router
    app.include_router(agent_mesh_stability_router.router)
    print("[OK] Included router: app.api.agent_mesh_stability_router.router")
except Exception as e:
    print(f"[WARN] Router skipped: app.api.agent_mesh_stability_router -> {e}")

try:
    from app.api import agent_mesh_real_exec_router
    app.include_router(agent_mesh_real_exec_router.router)
    print("[OK] Included router: app.api.agent_mesh_real_exec_router.router")
except Exception as e:
    print(f"[WARN] Router skipped: app.api.agent_mesh_real_exec_router -> {e}")

try:
    from app.api import agent_mesh_improvement_router
    app.include_router(agent_mesh_improvement_router.router)
    print("[OK] Included router: app.api.agent_mesh_improvement_router.router")
except Exception as e:
    print(f"[WARN] Router skipped: app.api.agent_mesh_improvement_router -> {e}")
# --- jarvis n8n bridge router ---
try:
    from app.routers.n8n_bridge_router import router as n8n_bridge_router
    app.include_router(n8n_bridge_router)
except Exception as jarvis_n8n_bridge_exc:
    print(f"[jarvis:n8n] router load failed: {jarvis_n8n_bridge_exc}")
# --- jarvis supervisor automation router ---
try:
    from app.routers.supervisor_automation_router import router as supervisor_automation_router
    app.include_router(supervisor_automation_router)
except Exception as jarvis_supervisor_automation_exc:
    print(f"[jarvis:supervisor_automation] router load failed: {jarvis_supervisor_automation_exc}")
# --- jarvis supervisor pipeline router ---
try:
    from app.routers.supervisor_pipeline_router import router as supervisor_pipeline_router
    app.include_router(supervisor_pipeline_router)
except Exception as jarvis_supervisor_pipeline_exc:
    print(f"[jarvis:supervisor_pipeline] router load failed: {jarvis_supervisor_pipeline_exc}")
# --- jarvis n8n canvas builder router ---
try:
    from app.routers.n8n_canvas_builder_router import router as n8n_canvas_builder_router
    app.include_router(n8n_canvas_builder_router)
except Exception as jarvis_n8n_canvas_builder_exc:
    print(f"[jarvis:n8n_canvas_builder] router load failed: {jarvis_n8n_canvas_builder_exc}")
# --- jarvis n8n workflow materializer router ---
try:
    from app.routers.n8n_workflow_materializer_router import router as n8n_workflow_materializer_router
    app.include_router(n8n_workflow_materializer_router)
except Exception as jarvis_n8n_workflow_materializer_exc:
    print(f"[jarvis:n8n_workflow_materializer] router load failed: {jarvis_n8n_workflow_materializer_exc}")

from app.routers.n8n_action_router_materializer_router import router as n8n_action_router_materializer_router
app.include_router(n8n_action_router_materializer_router)
print("[jarvis:n8n_materializer] router attached")
# --- JARVIS_N8N_FORCE_ATTACH_BEGIN ---
try:
    from app.routers.n8n_action_router_materializer_router import router as n8n_action_router_materializer_router
    _jarvis_n8n_paths = {getattr(_r, "path", None) for _r in app.routes if getattr(_r, "path", None)}
    if "/api/n8n/materializer/debug-state" not in _jarvis_n8n_paths:
        app.include_router(n8n_action_router_materializer_router)
        print("[jarvis:n8n_materializer] force-attach mounted debug router")
    else:
        print("[jarvis:n8n_materializer] force-attach skipped; debug router already mounted")
except Exception as exc:
    print(f"[jarvis:n8n_materializer] force-attach failed: {exc}")
# --- JARVIS_N8N_FORCE_ATTACH_END ---
# --- JARVIS_N8N_V2_FORCE_ATTACH_BEGIN ---
try:
    from app.routers.n8n_action_router_materializer_v2_router import router as n8n_action_router_materializer_v2_router
    _jarvis_n8n_v2_paths = {getattr(_r, "path", None) for _r in app.routes if getattr(_r, "path", None)}
    if "/api/n8n/materializer-v2/debug-state" not in _jarvis_n8n_v2_paths:
        app.include_router(n8n_action_router_materializer_v2_router)
        print("[jarvis:n8n_materializer_v2] force-attach mounted")
    else:
        print("[jarvis:n8n_materializer_v2] force-attach skipped; already mounted")
except Exception as exc:
    print(f"[jarvis:n8n_materializer_v2] force-attach failed: {exc}")
# --- JARVIS_N8N_V2_FORCE_ATTACH_END ---
# --- JARVIS N8N BRIDGE ROUTER MOUNT BEGIN ---
try:
    from app.routers.jarvis_n8n_bridge_router import router as jarvis_n8n_bridge_router
    _jarvis_n8n_paths = {getattr(_r, "path", None) for _r in app.routes if getattr(_r, "path", None)}
    if "/api/jarvis/n8n/health" not in _jarvis_n8n_paths:
        app.include_router(jarvis_n8n_bridge_router)
        print("[jarvis:n8n_bridge] mounted")
    else:
        print("[jarvis:n8n_bridge] already mounted")
except Exception as exc:
    print(f"[jarvis:n8n_bridge] mount failed: {exc}")
# --- JARVIS N8N BRIDGE ROUTER MOUNT END ---

# === JARVIS SUPERVISOR ROUTER HARDENING INSTALL ===
try:
    from app.jarvis_supervisor_router_hardening import install as _jarvis_install_supervisor_router_hardening
    _jarvis_install_supervisor_router_hardening(app)
except Exception as _jarvis_router_hardening_exc:
    print(f"[jarvis_supervisor_router_hardening] install skipped: {_jarvis_router_hardening_exc}")

# === Jarvis Unified Night API Router Integration ===
from app.routers.jarvis_unified_night_router import router as unified_night_router
app.include_router(unified_night_router)
# === /Jarvis Unified Night API Router Integration ===

# === Jarvis Operator Task Center Router Integration ===
from app.routers.jarvis_operator_task_router import router as operator_task_router
app.include_router(operator_task_router)
# === /Jarvis Operator Task Center Router Integration ===

# === Jarvis n8n Specialist Router Integration ===
from app.routers.jarvis_n8n_specialist_router import router as n8n_specialist_router
app.include_router(n8n_specialist_router)
# === /Jarvis n8n Specialist Router Integration ===

# === Jarvis n8n Super Agent Router Integration ===
from app.routers.jarvis_n8n_super_agent_router import router as n8n_super_agent_router
app.include_router(n8n_super_agent_router)
# === /Jarvis n8n Super Agent Router Integration ===

# === Jarvis Brain Foundation Router Integration ===
from app.routers.jarvis_brain_router import router as jarvis_brain_router
app.include_router(jarvis_brain_router)
# === /Jarvis Brain Foundation Router Integration ===

# === Jarvis Brain Executor Router Integration ===
from app.routers.jarvis_brain_executor_router import router as jarvis_brain_executor_router
app.include_router(jarvis_brain_executor_router)
# === /Jarvis Brain Executor Router Integration ===

# Jarvis Operator Dashboard router - added by V8.5 fixed integration
app.include_router(operator_dashboard.router)

@app.get("/")
def root():
    return {
        "status": "ok",
        "service": "jarvis_v3_supervisor",
        "message": "Jarvis backend is online",
    }

# === Jarvis V5 async content factory bridge ===
try:
    from app.routers.jarvis_v5_async_bridge import router as jarvis_v5_async_bridge_router
    app.include_router(jarvis_v5_async_bridge_router)
except Exception as e:
    print("WARN: failed to include jarvis_v5_async_bridge_router:", e)

# === Jarvis Internet Tools Router ===
try:
    from app.routers.jarvis_internet_tools_router import router as jarvis_internet_tools_router
    app.include_router(jarvis_internet_tools_router)
except Exception as e:
    print("WARN: failed to include jarvis_internet_tools_router:", e)

# === Jarvis AI Engineer Router ===
try:
    from app.routers.jarvis_ai_engineer_router import router as jarvis_ai_engineer_router
    app.include_router(jarvis_ai_engineer_router)
except Exception as e:
    print("WARN: failed to include jarvis_ai_engineer_router:", e)

# === Jarvis Telegram File Tools Router ===
try:
    from app.routers.jarvis_telegram_file_tools_router import router as jarvis_telegram_file_tools_router
    app.include_router(jarvis_telegram_file_tools_router)
except Exception as e:
    print("WARN: failed to include jarvis_telegram_file_tools_router:", e)
