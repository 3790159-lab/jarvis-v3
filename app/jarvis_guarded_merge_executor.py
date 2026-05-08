from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

MODULE_ID = "jarvis_guarded_merge_executor"
MODULE_VERSION = "1.0.0"

PROJECT_ROOT = Path(r"C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram")
ROOT_DIR = PROJECT_ROOT / "jarvis_stage3_artifacts" / "guarded_merge_executor"
PACKAGES_DIR = ROOT_DIR / "packages"
BLUEPRINTS_DIR = ROOT_DIR / "blueprints"
MODULES_DIR = PROJECT_ROOT / "jarvis_stage3_artifacts" / "generated_modules"
CONFIG_PATH = ROOT_DIR / "config.json"
STATE_PATH = ROOT_DIR / "state.json"

for _p in [ROOT_DIR, PACKAGES_DIR, BLUEPRINTS_DIR, MODULES_DIR]:
    _p.mkdir(parents=True, exist_ok=True)

DEFAULT_CONFIG: Dict[str, Any] = {
    "service_port": 8029,
    "self_evolution_base_url": "http://127.0.0.1:8028",
    "supervisor_base_url": "http://127.0.0.1:8015",
    "director_base_url": "http://127.0.0.1:8024",
    "bridge_base_url": "http://127.0.0.1:8030",
    "allow_core_live_apply": False,
    "auto_package_approved_plans": True,
    "allow_additive_module_scaffolds": True,
    "module_default_port_base": 8110,
    "allowed_core_plan_kinds": [
        "supervisor_router_hardening",
        "director_lifecycle_tuneup",
    ],
    "allowed_module_blueprints": [
        "telegram_message_responder_agent",
        "operator_notifier_agent",
        "workflow_sentinel_agent",
        "research_planner_agent",
    ],
    "updated_at": None,
}

DEFAULT_STATE: Dict[str, Any] = {
    "state_id": "jarvis_guarded_merge_executor_state",
    "version": 1,
    "packaged_plans": {},
    "blueprints": {},
    "modules": {},
    "history": [],
}

_state_lock = threading.RLock()
app = FastAPI(title="Jarvis Guarded Merge Executor", version=MODULE_VERSION)

class ConfigUpdateRequest(BaseModel):
    allow_core_live_apply: Optional[bool] = None
    auto_package_approved_plans: Optional[bool] = None
    allow_additive_module_scaffolds: Optional[bool] = None
    module_default_port_base: Optional[int] = None

class PackageApprovedRequest(BaseModel):
    kind: Optional[str] = None
    proposal_id: Optional[str] = None

class ScaffoldModuleRequest(BaseModel):
    blueprint_id: str
    module_name: str
    display_name: Optional[str] = None
    purpose: Optional[str] = None

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def now_iso() -> str:
    return now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")

def slugify(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_\-]+", "-", value.strip().lower())
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value or f"item-{uuid.uuid4().hex[:6]}"

def load_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        save_json(path, default)
        return json.loads(json.dumps(default))
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        save_json(path, default)
        return json.loads(json.dumps(default))

def save_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)

def load_config() -> Dict[str, Any]:
    data = load_json(CONFIG_PATH, DEFAULT_CONFIG)
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    merged.update(data or {})
    merged["updated_at"] = merged.get("updated_at") or now_iso()
    return merged

def save_config(config: Dict[str, Any]) -> None:
    config["updated_at"] = now_iso()
    save_json(CONFIG_PATH, config)

def load_state() -> Dict[str, Any]:
    data = load_json(STATE_PATH, DEFAULT_STATE)
    merged = json.loads(json.dumps(DEFAULT_STATE))
    merged.update(data or {})
    merged.setdefault("packaged_plans", {})
    merged.setdefault("blueprints", {})
    merged.setdefault("modules", {})
    merged.setdefault("history", [])
    return merged

def save_state(state: Dict[str, Any]) -> None:
    save_json(STATE_PATH, state)

def push_history(state: Dict[str, Any], event: Dict[str, Any]) -> None:
    state.setdefault("history", []).append(event)
    state["history"] = state["history"][-300:]

def fetch_json(url: str, timeout: int = 20) -> Dict[str, Any]:
    try:
        resp = requests.get(url, timeout=timeout)
        try:
            body = resp.json()
        except Exception:
            body = resp.text
        return {"ok": 200 <= resp.status_code < 300, "status_code": resp.status_code, "body": body, "url": url}
    except Exception as exc:
        return {"ok": False, "status_code": 0, "body": str(exc), "url": url}

def fetch_self_evolution_proposals(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    resp = fetch_json(f"{config['self_evolution_base_url']}/api/self-evolution/proposals", timeout=30)
    body = resp.get("body") or {}
    proposals = body.get("proposals") or []
    return [p for p in proposals if isinstance(p, dict)]

def discover_approved_plan_only(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    allowed = set(config.get("allowed_core_plan_kinds", []))
    out: List[Dict[str, Any]] = []
    for p in fetch_self_evolution_proposals(config):
        if p.get("kind") not in allowed:
            continue
        if p.get("status") != "applied":
            continue
        result = p.get("result") or {}
        plan_file = result.get("plan_file")
        if not plan_file:
            continue
        out.append({
            "proposal_id": p.get("id"),
            "kind": p.get("kind"),
            "title": p.get("title"),
            "plan_file": plan_file,
            "files_modify": p.get("files_modify") or [],
            "files_create": p.get("files_create") or [],
            "approval_deadline": p.get("approval_deadline"),
            "updated_at": p.get("updated_at"),
            "result": result,
        })
    return sorted(out, key=lambda x: x.get("updated_at") or "")

def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""

def make_package_dir(kind: str, proposal_id: str) -> Path:
    folder = f"{slugify(kind)}__{slugify(proposal_id)}"
    path = PACKAGES_DIR / folder
    path.mkdir(parents=True, exist_ok=True)
    return path

def write_file(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return str(path)

def service_smoke_script(config: Dict[str, Any]) -> str:
    return "\n".join([
        "Set-StrictMode -Version Latest",
        '$ErrorActionPreference = "Stop"',
        "",
        f'$Supervisor = Invoke-RestMethod -Method GET -Uri "{config["supervisor_base_url"]}/health" -TimeoutSec 15',
        f'$Director   = Invoke-RestMethod -Method GET -Uri "{config["director_base_url"]}/health" -TimeoutSec 15',
        f'$Bridge     = Invoke-RestMethod -Method GET -Uri "{config["bridge_base_url"]}/health" -TimeoutSec 15',
        "",
        '$Result = [ordered]@{ supervisor = $Supervisor; director = $Director; bridge = $Bridge }',
        '$Result | ConvertTo-Json -Depth 50',
        "",
    ])

def backup_targets_script(targets: List[str]) -> str:
    lines = [
        "Set-StrictMode -Version Latest",
        '$ErrorActionPreference = "Stop"',
        f'$ProjectRoot = "{PROJECT_ROOT}"',
        '$OutDir = Join-Path $ProjectRoot ("jarvis_stage3_artifacts\\guarded_merge_executor\\backups\\" + (Get-Date -Format "yyyyMMdd_HHmmss"))',
        'New-Item -ItemType Directory -Path $OutDir -Force | Out-Null',
        '$Targets = @(',
    ]
    for t in targets:
        lines.append(f'    "{t}",')
    lines.extend([
        ')',
        'foreach ($T in $Targets) {',
        '    $Full = Join-Path $ProjectRoot $T',
        '    if (Test-Path $Full) {',
        '        $Safe = $T -replace "[\\\\/:*?\"\"<>|]", "__"',
        '        Copy-Item -Path $Full -Destination (Join-Path $OutDir $Safe) -Recurse -Force -ErrorAction SilentlyContinue',
        '    }',
        '}',
        'Write-Host $OutDir -ForegroundColor Cyan',
        '',
    ])
    return "\n".join(lines)

def apply_candidate_script(kind: str, package_dir: Path) -> str:
    return "\n".join([
        "Set-StrictMode -Version Latest",
        '$ErrorActionPreference = "Stop"',
        '',
        'param([switch]$AllowCoreApply)',
        '',
        'if (-not $AllowCoreApply) {',
        f'    throw "Core live apply is disabled for package: {kind}. Review package docs and use guarded merge only after explicit enable."',
        '}',
        '',
        f'Write-Host "Core live apply placeholder for {kind}" -ForegroundColor Yellow',
        f'Write-Host "Review package: {package_dir}" -ForegroundColor Cyan',
        '',
    ])

def generate_package_from_plan(item: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    proposal_id = str(item["proposal_id"])
    kind = str(item["kind"])
    plan_path = Path(str(item["plan_file"]))
    if not plan_path.exists():
        raise RuntimeError(f"Plan file missing: {plan_path}")

    plan_text = read_text(plan_path)
    package_dir = make_package_dir(kind, proposal_id)

    manifest = {
        "package_id": f"gme-{kind}-{proposal_id}",
        "kind": kind,
        "proposal_id": proposal_id,
        "title": item.get("title"),
        "plan_file": str(plan_path),
        "targets": item.get("files_modify") or [],
        "created_at": now_iso(),
        "mode": "guarded_merge_package",
        "allow_core_live_apply": bool(config.get("allow_core_live_apply", False)),
        "requires_manual_merge": True,
    }

    summary_md = "\n".join([
        f"# Guarded Merge Package: {kind}",
        "",
        f"- Proposal ID: `{proposal_id}`",
        f"- Title: `{item.get('title')}`",
        f"- Created at: `{manifest['created_at']}`",
        f"- Plan file: `{plan_path}`",
        f"- Requires manual merge: `{manifest['requires_manual_merge']}`",
        "",
        "## Targets",
        *[f"- `{t}`" for t in (item.get("files_modify") or [])],
        "",
        "## Safety flow",
        "1. Review the original plan file.",
        "2. Run `backup_targets.ps1`.",
        "3. Review `candidate_patch_notes.md`.",
        "4. Create or refine a real patch block.",
        "5. Run compile + health + smoke.",
        "6. Only then consider live merge.",
        "",
    ])

    candidate_notes_md = "\n".join([
        f"# Candidate Patch Notes: {kind}",
        "",
        "## Original plan content",
        "```text",
        plan_text[:12000],
        "```",
        "",
        "## Generated guarded recommendations",
        "- keep changes additive where possible",
        "- preserve existing routes / contracts",
        "- create backups before modifying target files",
        "- compile before restart",
        "- run health + smoke checks after any live merge",
        "",
    ])

    manifest_path = write_file(package_dir / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    summary_path = write_file(package_dir / "README.md", summary_md)
    notes_path = write_file(package_dir / "candidate_patch_notes.md", candidate_notes_md)
    backup_path = write_file(package_dir / "backup_targets.ps1", backup_targets_script(item.get("files_modify") or []))
    smoke_path = write_file(package_dir / "smoke_after.ps1", service_smoke_script(config))
    apply_path = write_file(package_dir / "apply_candidate.ps1", apply_candidate_script(kind, package_dir))

    return {
        "package_dir": str(package_dir),
        "manifest_path": manifest_path,
        "summary_path": summary_path,
        "candidate_notes_path": notes_path,
        "backup_script": backup_path,
        "smoke_script": smoke_path,
        "apply_script": apply_path,
        "proposal_id": proposal_id,
        "kind": kind,
    }

def default_blueprints(config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        "telegram_message_responder_agent": {
            "blueprint_id": "telegram_message_responder_agent",
            "display_name": "Telegram Message Responder Agent",
            "risk": "additive_low",
            "summary": "Managed module skeleton for Telegram message intake and supervised response routing.",
            "ports_offset": 0,
            "default_files": ["manifest.json", "README.md", "app.py", "scripts/start_module.ps1", "config/module.env.example"],
        },
        "operator_notifier_agent": {
            "blueprint_id": "operator_notifier_agent",
            "display_name": "Operator Notifier Agent",
            "risk": "additive_low",
            "summary": "Managed notifier module for operator alerts, digests and escalation messages.",
            "ports_offset": 1,
            "default_files": ["manifest.json", "README.md", "app.py", "scripts/start_module.ps1", "config/module.env.example"],
        },
        "workflow_sentinel_agent": {
            "blueprint_id": "workflow_sentinel_agent",
            "display_name": "Workflow Sentinel Agent",
            "risk": "additive_low",
            "summary": "Managed watcher module for workflow health, timeout detection and self-check alerts.",
            "ports_offset": 2,
            "default_files": ["manifest.json", "README.md", "app.py", "scripts/start_module.ps1", "config/module.env.example"],
        },
        "research_planner_agent": {
            "blueprint_id": "research_planner_agent",
            "display_name": "Research Planner Agent",
            "risk": "additive_low",
            "summary": "Managed planning module for research queues, evidence packs and task decomposition.",
            "ports_offset": 3,
            "default_files": ["manifest.json", "README.md", "app.py", "scripts/start_module.ps1", "config/module.env.example"],
        },
    }

def register_blueprints_in_state(state: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    defaults = default_blueprints(config)
    allowed = set(config.get("allowed_module_blueprints", []))
    for key, bp in defaults.items():
        if key in allowed:
            state.setdefault("blueprints", {})[key] = bp
            write_file(BLUEPRINTS_DIR / f"{key}.json", json.dumps(bp, ensure_ascii=False, indent=2))
    return state["blueprints"]

def module_port(config: Dict[str, Any], state: Dict[str, Any], blueprint_id: str) -> int:
    bp = state.get("blueprints", {}).get(blueprint_id) or {}
    base = int(config.get("module_default_port_base", 8110))
    offset = int(bp.get("ports_offset", 0))
    return base + offset + len(state.get("modules", {}))

def module_app_py(module_name: str, display_name: str, purpose: str) -> str:
    return "\n".join([
        "from __future__ import annotations",
        "",
        "from datetime import datetime, timezone",
        "from typing import Any, Dict",
        "",
        "from fastapi import FastAPI",
        "",
        f'MODULE_ID = "{module_name}"',
        'MODULE_VERSION = "0.1.0"',
        f'DISPLAY_NAME = "{display_name}"',
        f'PURPOSE = "{purpose}"',
        "",
        'app = FastAPI(title=DISPLAY_NAME, version=MODULE_VERSION)',
        "",
        "def now_iso() -> str:",
        '    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")',
        "",
        '@app.get("/health")',
        "def health() -> Dict[str, Any]:",
        '    return {"status": "healthy", "service": MODULE_ID, "version": MODULE_VERSION, "purpose": PURPOSE, "ts": now_iso()}',
        "",
        '@app.get("/__whoami")',
        "def whoami() -> Dict[str, Any]:",
        '    return {"status": "ok", "service": MODULE_ID, "display_name": DISPLAY_NAME, "purpose": PURPOSE}',
        "",
        '@app.post("/api/module/handle")',
        "def handle(payload: Dict[str, Any]) -> Dict[str, Any]:",
        '    return {"status": "queued", "service": MODULE_ID, "received": payload, "note": "Module scaffold created. Integrate business logic next."}',
        "",
    ])

def module_readme(module_name: str, display_name: str, purpose: str, port: int, blueprint_id: str) -> str:
    return "\n".join([
        f"# {display_name}",
        "",
        f"- Module ID: `{module_name}`",
        f"- Blueprint: `{blueprint_id}`",
        f"- Purpose: `{purpose}`",
        f"- Default port: `{port}`",
        "",
        "## What this scaffold gives you",
        "- isolated managed module workspace",
        "- own FastAPI app with health and handle endpoints",
        "- own start script and env example",
        "- safe additive structure without touching core runtime",
        "",
        "## Typical next steps",
        "- add actual integration logic",
        "- connect secrets in module.env",
        "- run smoke tests",
        "- wire into Supervisor/Telegram only after review",
        "",
    ])

def module_start_ps1(module_name: str, port: int) -> str:
    return "\n".join([
        "param(",
        f'    [string]$ProjectRoot = "{PROJECT_ROOT}",',
        f'    [int]$Port = {port}',
        ")",
        "",
        "Set-StrictMode -Version Latest",
        '$ErrorActionPreference = "Stop"',
        "",
        '$PyExeCandidate = Join-Path $ProjectRoot ".venv\\Scripts\\python.exe"',
        '$PyExe = if (Test-Path $PyExeCandidate) { $PyExeCandidate } else { "python" }',
        f'$ModuleDir = Join-Path $ProjectRoot "jarvis_stage3_artifacts\\generated_modules\\{module_name}"',
        'Set-Location -Path $ModuleDir',
        '& $PyExe -X utf8 -m uvicorn app:app --host 127.0.0.1 --port $Port',
        "",
    ])

def module_env_example(module_name: str, port: int) -> str:
    return "\n".join([
        f"MODULE_ID={module_name}",
        f"MODULE_PORT={port}",
        "TELEGRAM_BOT_TOKEN=",
        "TELEGRAM_ALLOWED_CHAT_ID=",
        "OPENAI_API_KEY=",
        "OLLAMA_BASE_URL=http://127.0.0.1:11434",
        "NOTES=Fill secrets before wiring real integrations",
        "",
    ])

def scaffold_module(state: Dict[str, Any], config: Dict[str, Any], req: ScaffoldModuleRequest) -> Dict[str, Any]:
    if not config.get("allow_additive_module_scaffolds", True):
        raise HTTPException(status_code=403, detail="Additive module scaffolds are disabled")
    bp = state.get("blueprints", {}).get(req.blueprint_id)
    if not bp:
        raise HTTPException(status_code=404, detail="Blueprint not found")

    module_name = slugify(req.module_name)
    if module_name in state.get("modules", {}):
        return state["modules"][module_name]

    display_name = req.display_name or bp.get("display_name") or module_name
    purpose = req.purpose or bp.get("summary") or "Managed module scaffold"
    port = module_port(config, state, req.blueprint_id)

    module_dir = MODULES_DIR / module_name
    (module_dir / "scripts").mkdir(parents=True, exist_ok=True)
    (module_dir / "config").mkdir(parents=True, exist_ok=True)

    manifest = {
        "module_id": module_name,
        "display_name": display_name,
        "blueprint_id": req.blueprint_id,
        "purpose": purpose,
        "port": port,
        "created_at": now_iso(),
        "mode": "managed_module_scaffold",
        "status": "scaffolded",
    }

    write_file(module_dir / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    write_file(module_dir / "README.md", module_readme(module_name, display_name, purpose, port, req.blueprint_id))
    write_file(module_dir / "app.py", module_app_py(module_name, display_name, purpose))
    write_file(module_dir / "scripts" / "start_module.ps1", module_start_ps1(module_name, port))
    write_file(module_dir / "config" / "module.env.example", module_env_example(module_name, port))

    result = {
        "module_id": module_name,
        "display_name": display_name,
        "blueprint_id": req.blueprint_id,
        "purpose": purpose,
        "port": port,
        "module_dir": str(module_dir),
        "status": "scaffolded",
        "created_at": manifest["created_at"],
    }
    state.setdefault("modules", {})[module_name] = result
    push_history(state, {"ts": now_iso(), "event": "module_scaffolded", "module_id": module_name, "blueprint_id": req.blueprint_id})
    return result

def auto_package_approved(state: Dict[str, Any], config: Dict[str, Any]) -> List[Dict[str, Any]]:
    packaged: List[Dict[str, Any]] = []
    if not config.get("auto_package_approved_plans", True):
        return packaged
    existing = state.setdefault("packaged_plans", {})
    for item in discover_approved_plan_only(config):
        proposal_id = str(item["proposal_id"])
        if proposal_id in existing:
            continue
        pkg = generate_package_from_plan(item, config)
        existing[proposal_id] = pkg
        packaged.append(pkg)
        push_history(state, {"ts": now_iso(), "event": "plan_packaged", "proposal_id": proposal_id, "kind": item["kind"]})
    return packaged

@app.get("/health")
def health() -> Dict[str, Any]:
    state = load_state()
    return {
        "status": "healthy",
        "service": MODULE_ID,
        "version": MODULE_VERSION,
        "packaged_plans": len(state.get("packaged_plans", {})),
        "blueprints": len(state.get("blueprints", {})),
        "modules": len(state.get("modules", {})),
        "ts": now_iso(),
    }

@app.get("/__whoami")
def whoami() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": MODULE_ID,
        "version": MODULE_VERSION,
        "routes": sorted([getattr(r, "path", "") for r in app.routes if getattr(r, "path", "")]),
    }

@app.get("/api/guarded-merge/config")
def get_config() -> Dict[str, Any]:
    return {"status": "ok", "service": MODULE_ID, "config": load_config()}

@app.post("/api/guarded-merge/config/update")
def update_config(req: ConfigUpdateRequest) -> Dict[str, Any]:
    config = load_config()
    update = req.model_dump(exclude_none=True)
    for k, v in update.items():
        config[k] = v
    save_config(config)
    return {"status": "ok", "service": MODULE_ID, "config": config}

@app.get("/api/guarded-merge/status")
def status() -> Dict[str, Any]:
    with _state_lock:
        config = load_config()
        state = load_state()
        return {
            "status": "ok",
            "service": MODULE_ID,
            "config": config,
            "counts": {
                "packaged_plans": len(state.get("packaged_plans", {})),
                "blueprints": len(state.get("blueprints", {})),
                "modules": len(state.get("modules", {})),
            },
            "packaged_plans": state.get("packaged_plans", {}),
            "blueprints": state.get("blueprints", {}),
            "modules": state.get("modules", {}),
            "history": state.get("history", [])[-100:],
        }

@app.get("/api/guarded-merge/discover-approved")
def discover_approved() -> Dict[str, Any]:
    config = load_config()
    items = discover_approved_plan_only(config)
    return {"status": "ok", "service": MODULE_ID, "approved_plan_only": items}

@app.post("/api/guarded-merge/package-approved")
def package_approved(req: PackageApprovedRequest) -> Dict[str, Any]:
    with _state_lock:
        config = load_config()
        state = load_state()
        items = discover_approved_plan_only(config)
        chosen: Optional[Dict[str, Any]] = None

        if req.proposal_id:
            for item in items:
                if item["proposal_id"] == req.proposal_id:
                    chosen = item
                    break
        elif req.kind:
            filtered = [i for i in items if i["kind"] == req.kind]
            if filtered:
                chosen = filtered[-1]

        if not chosen:
            raise HTTPException(status_code=404, detail="Approved plan not found")

        pkg = generate_package_from_plan(chosen, config)
        state.setdefault("packaged_plans", {})[chosen["proposal_id"]] = pkg
        push_history(state, {"ts": now_iso(), "event": "plan_packaged", "proposal_id": chosen["proposal_id"], "kind": chosen["kind"]})
        save_state(state)
        return {"status": "ok", "service": MODULE_ID, "package": pkg}

@app.post("/api/guarded-merge/package-all-approved")
def package_all_approved() -> Dict[str, Any]:
    with _state_lock:
        config = load_config()
        state = load_state()
        packaged = auto_package_approved(state, config)
        save_state(state)
        return {"status": "ok", "service": MODULE_ID, "packaged": packaged, "packaged_count": len(packaged)}

@app.post("/api/guarded-merge/blueprints/register-defaults")
def register_defaults() -> Dict[str, Any]:
    with _state_lock:
        config = load_config()
        state = load_state()
        blueprints = register_blueprints_in_state(state, config)
        save_state(state)
        return {"status": "ok", "service": MODULE_ID, "blueprints": blueprints}

@app.get("/api/guarded-merge/blueprints")
def blueprints() -> Dict[str, Any]:
    state = load_state()
    return {"status": "ok", "service": MODULE_ID, "blueprints": state.get("blueprints", {})}

@app.post("/api/guarded-merge/modules/scaffold")
def scaffold(req: ScaffoldModuleRequest) -> Dict[str, Any]:
    with _state_lock:
        config = load_config()
        state = load_state()
        if not state.get("blueprints"):
            register_blueprints_in_state(state, config)
        result = scaffold_module(state, config, req)
        save_state(state)
        return {"status": "ok", "service": MODULE_ID, "module": result}

@app.get("/api/guarded-merge/modules")
def modules() -> Dict[str, Any]:
    state = load_state()
    return {"status": "ok", "service": MODULE_ID, "modules": state.get("modules", {})}

@app.on_event("startup")
def startup() -> None:
    config = load_config()
    save_config(config)
    state = load_state()
    register_blueprints_in_state(state, config)
    auto_package_approved(state, config)
    save_state(state)