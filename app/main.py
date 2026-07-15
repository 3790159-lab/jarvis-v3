from __future__ import annotations
# ── .env auto-reload (Phase H1.2) ─────────────────────────────────────────
import os as _os
from pathlib import Path as _Path

def _load_dotenv() -> None:
    """Load .env variables that are not already set in the environment."""
    env_file = _Path(__file__).parent.parent / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and not _os.getenv(key):
            _os.environ[key] = value

_load_dotenv()

# --- File logging (M.1.5 #2) ---
from app.core.logging_setup import setup_app_logging  # noqa: E402
setup_app_logging()  # noqa: E402
# ── end .env auto-reload ───────────────────────────────────────────────────

from app.routers.multi_ai_orchestrator_v1 import router as multi_ai_orchestrator_v1_router
from app.routers.jarvis_brain_v2 import router as jarvis_brain_v2_router
try:
    from app.routers.jarvis_v5_content_factory import router as jarvis_v5_content_factory_router
    _jarvis_v5_content_factory_available = True
except Exception as _jarvis_v5_content_factory_exc:
    print(f"[jarvis:v5_content_factory] router load failed: {_jarvis_v5_content_factory_exc}")
    _jarvis_v5_content_factory_available = False
from app.routers.jarvis_live_operator import router as jarvis_live_operator_router
from app.routers import operator_dashboard
from app.routers import time_brain




import importlib
import logging
import os
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException, Request
from app.routers import claude_ecosystem
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
app.include_router(multi_ai_orchestrator_v1_router)  # Jarvis Multi-AI Orchestrator V1
app.include_router(jarvis_brain_v2_router)  # Jarvis Brain V2 identity/router override

try:
    from app.routers.jarvis_logs_router import router as jarvis_logs_router
    app.include_router(jarvis_logs_router)
except Exception as _logs_exc:
    print(f"[jarvis:logs_router] load failed: {_logs_exc}")

try:
    from app.routers.jarvis_file_tools_router import router as jarvis_file_tools_router
    app.include_router(jarvis_file_tools_router)
except Exception as _file_tools_exc:
    print(f"[jarvis:file_tools_router] load failed: {_file_tools_exc}")
if _jarvis_v5_content_factory_available:
    app.include_router(jarvis_v5_content_factory_router)
app.include_router(jarvis_live_operator_router)
app.include_router(claude_ecosystem.router)
app.include_router(time_brain.router)

try:
    from app.routers.oauth_callback_router import router as oauth_callback_router
    app.include_router(oauth_callback_router)
except Exception as _oauth_cb_exc:
    print(f"[jarvis:oauth_callback_router] load failed: {_oauth_cb_exc}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Default-deny auth guard (audit 2026-07-15). Phase 1: canary (log-only) — does
# nothing unless JARVIS_AUTH_MIDDLEWARE_MODE=canary. Safe to deploy at "off".
from app.services.auth_middleware import auth_guard_middleware  # noqa: E402

app.middleware("http")(auth_guard_middleware)


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "healthy",
        "service": "jarvis_v3_supervisor",
        "env": os.getenv("APP_ENV", "dev"),
    }


@app.get("/health/detailed")
def health_detailed() -> Dict[str, Any]:
    """Extended health check with agent status, errors, uptime."""
    import time as _time
    from pathlib import Path as _Path

    start_time = getattr(health_detailed, "_start_time", None)
    if start_time is None:
        health_detailed._start_time = _time.time()
        start_time = health_detailed._start_time
    uptime_seconds = int(_time.time() - start_time)

    # Count scheduled tasks
    scheduled_count = 0
    try:
        tasks_path = _Path("state") / "scheduled_tasks.json"
        if tasks_path.exists():
            import json as _json
            tasks = _json.loads(tasks_path.read_text(encoding="utf-8"))
            scheduled_count = len([t for t in tasks if t.get("active")])
    except Exception:
        pass

    # Count errors in last hour
    errors_last_hour = 0
    try:
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        import json as _json
        errors_path = _Path("state") / "errors.log"
        if errors_path.exists():
            cutoff = (_dt.now(_tz.utc) - _td(hours=1)).isoformat()
            for line in errors_path.read_text(encoding="utf-8").splitlines():
                try:
                    rec = _json.loads(line)
                    if rec.get("timestamp", "") > cutoff:
                        errors_last_hour += 1
                except Exception:
                    pass
    except Exception:
        pass

    # Agent status
    agents = {
        "perplexity": bool(os.getenv("PERPLEXITY_API_KEY") or os.getenv("PPLX_API_KEY")),
        "openai": bool(os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY")),
        "anthropic": bool(os.getenv("ANTHROPIC_API_KEY")),
        "n8n": bool(os.getenv("N8N_API_KEY")),
    }

    return {
        "status": "healthy",
        "service": "jarvis_v3_supervisor",
        "env": os.getenv("APP_ENV", "dev"),
        "uptime_seconds": uptime_seconds,
        "scheduled_tasks": scheduled_count,
        "errors_last_hour": errors_last_hour,
        "agents": {k: "configured" if v else "not_configured" for k, v in agents.items()},
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


# === Jarvis Status Dashboard (Phase 40) — mount BEFORE generic ROUTER_CANDIDATES
try:
    from app.routers.jarvis_dashboard_router import router as jarvis_dashboard_router
    app.include_router(jarvis_dashboard_router)
    print("[OK] Included router: jarvis_dashboard_router")
except Exception as e:
    print(f"WARN: failed to include jarvis_dashboard_router: {e}")

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
async def startup_event() -> None:
    print("[INFO] Jarvis backend startup complete")
    import asyncio as _asyncio
    _asyncio.create_task(_periodic_cleanup())
    _asyncio.create_task(_watchdog_loop())

    # Activate Night Autonomy
    try:
        from app.services.night_workflows import NightWorkflow
        nw = NightWorkflow()
        nw.schedule_all_phases()
        print("[OK] Night Autonomy phases scheduled")
    except Exception as _e:
        print(f"[WARN] Night Autonomy not started: {_e}")


async def _watchdog_loop() -> None:
    """Background task: check bot heartbeat periodically, restart if dead.

    Interval is configurable via WATCHDOG_CHECK_INTERVAL_SEC (default 60s).
    """
    import asyncio as _asyncio
    while True:
        try:
            from app.services.system_watchdog import (
                restart_bot_if_dead,
                heartbeat_check_interval_sec,
            )
            restart_bot_if_dead()
            interval = heartbeat_check_interval_sec()
        except Exception as _exc:
            print(f"[WARN] watchdog_loop error: {_exc}")
            interval = 60
        await _asyncio.sleep(interval)


async def _periodic_cleanup() -> None:
    """Background task: run self-healing cleanup every 60 minutes."""
    import asyncio as _asyncio
    try:
        from app.services.self_healing import cleanup_old_logs, archive_old_decisions
    except Exception as exc:
        print(f"[WARN] self_healing import failed: {exc}")
        return

    while True:
        try:
            cleaned = cleanup_old_logs()
            archived = archive_old_decisions()
            if cleaned or archived:
                print(f"[cleanup] logs={cleaned} decisions_archived={archived}")
        except Exception as exc:
            print(f"[WARN] periodic_cleanup error: {exc}")
        await _asyncio.sleep(3600)

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

try:
    from app.routers.n8n_action_router_materializer_router import router as n8n_action_router_materializer_router
    app.include_router(n8n_action_router_materializer_router)
    print("[jarvis:n8n_materializer] router attached")
except Exception as jarvis_n8n_materializer_exc:
    print(f"[jarvis:n8n_materializer] router load failed: {jarvis_n8n_materializer_exc}")
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


# === Obsidian Save Router (Phase H1.3) ===
try:
    from app.routers.obsidian_save_router import router as obsidian_save_router
    app.include_router(obsidian_save_router)
    print("[OK] Included router: obsidian_save_router")
except Exception as e:
    print(f"WARN: failed to include obsidian_save_router: {e}")

# === Public API v1 (Phase 44) ===
try:
    from app.api.public_api_v1 import router as public_api_v1_router
    app.include_router(public_api_v1_router)
    print("[OK] Included router: public_api_v1_router")
except Exception as e:
    print(f"WARN: failed to include public_api_v1_router: {e}")

# === Replicate Image Generation Router (Phase 38) ===
try:
    from app.routers.replicate_image_router import router as replicate_image_router
    app.include_router(replicate_image_router)
    print("[OK] Included router: replicate_image_router")
except Exception as e:
    print(f"WARN: failed to include replicate_image_router: {e}")

# === Telegram Webhook Endpoint (Phase 37) ===
# Receives Telegram updates when webhook mode is active (e.g. via Cloudflare Tunnel)
_webhook_log = logging.getLogger("jarvis.telegram_webhook")


def _verify_webhook_secret(request: Request) -> None:
    """Reject (403) any webhook call without the correct Telegram secret token.

    Security audit 2026-07-15 (H1): forged Telegram updates let an anonymous
    caller impersonate the admin. Telegram sends the configured secret in the
    ``X-Telegram-Bot-Api-Secret-Token`` header (see setWebhook secret_token).

    Fails **closed**: if ``TELEGRAM_WEBHOOK_SECRET`` is unset, every request is
    rejected — the endpoint never accepts unauthenticated updates.
    """
    import secrets as _secrets

    expected = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
    provided = (request.headers.get("X-Telegram-Bot-Api-Secret-Token") or "").strip()

    ok = bool(expected) and bool(provided) and _secrets.compare_digest(provided, expected)
    if not ok:
        client_ip = request.client.host if request.client else "unknown"
        reason = "secret not configured" if not expected else "bad/missing secret token"
        _webhook_log.warning(
            "Rejected Telegram webhook (%s) from ip=%s path=%s",
            reason,
            client_ip,
            request.url.path,
        )
        raise HTTPException(status_code=403, detail="Forbidden")


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request) -> Dict[str, Any]:
    """Accept Telegram webhook updates and forward to bot processing queue."""
    # Authenticate BEFORE reading the body or touching the queue.
    _verify_webhook_secret(request)

    try:
        body = await request.json()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    # Write update to a queue file that the bot process reads
    import json as _json
    from pathlib import Path as _Path
    queue_path = _Path("state") / "webhook_queue.jsonl"
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with queue_path.open("a", encoding="utf-8") as f:
            f.write(_json.dumps(body, ensure_ascii=False) + "\n")
    except Exception as exc:
        return {"ok": False, "error": f"Queue write failed: {exc}"}

    return {"ok": True}


@app.get("/telegram/webhook/status")
def telegram_webhook_status() -> Dict[str, Any]:
    """Check webhook queue status."""
    from pathlib import Path as _Path
    queue = _Path("state") / "webhook_queue.jsonl"
    count = 0
    if queue.exists():
        count = len([l for l in queue.read_text(encoding="utf-8").splitlines() if l.strip()])
    return {"ok": True, "queued_updates": count}
