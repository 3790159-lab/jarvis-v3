from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

MODULE_ID = "jarvis_self_evolution_guarded"
MODULE_VERSION = "1.1.0"

PROJECT_ROOT = Path(r"C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram")
ROOT_DIR = PROJECT_ROOT / "jarvis_stage3_artifacts" / "self_evolution"
REPORTS_DIR = ROOT_DIR / "reports"
PATCHES_DIR = ROOT_DIR / "proposed_patches"
LOGS_DIR = PROJECT_ROOT / "jarvis_stage3_artifacts" / "logs"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
CONFIG_PATH = ROOT_DIR / "config.json"
STATE_PATH = ROOT_DIR / "state.json"
LATEST_REPORT_PATH = REPORTS_DIR / "latest.json"
MAX_LOG_TAIL_LINES = 300

for _p in [ROOT_DIR, REPORTS_DIR, PATCHES_DIR, LOGS_DIR, SCRIPTS_DIR]:
    _p.mkdir(parents=True, exist_ok=True)

DEFAULT_CONFIG: Dict[str, Any] = {
    "mode": "B_guarded_autonomy",
    "service_port": 8028,
    "approval_wait_seconds": 600,
    "runner_idle_sleep_seconds": 20,
    "runner_cycle_limit": 0,
    "night_default_hours": 8.0,
    "auto_apply_low_risk": True,
    "require_approval_medium_risk": True,
    "require_approval_high_risk": True,
    "skip_on_timeout": True,
    "focus_default": "reliability",
    "allowed_low_risk_actions": [
        "health_snapshot_pack",
        "regression_quick_pack",
        "backup_state_pack",
        "log_summary_pack",
        "inventory_snapshot_pack",
        "cleanup_audit_pack",
        "proposal_digest_pack",
        "runner_watchdog_pack",
    ],
    "allowed_medium_risk_actions": [
        "director_lifecycle_tuneup",
        "supervisor_router_hardening",
    ],
    "medium_risk_cooldown_minutes": 240,
    "max_skipped_per_run_per_kind": 1,
    "max_pending_medium_total": 1,
    "supervisor_base_url": "http://127.0.0.1:8015",
    "director_base_url": "http://127.0.0.1:8024",
    "bridge_base_url": "http://127.0.0.1:8030",
    "ai_consult_enabled": False,
    "ai_consult_url": "",
    "updated_at": None,
}

DEFAULT_STATE: Dict[str, Any] = {
    "state_id": "jarvis_self_evolution_guarded_state",
    "version": 2,
    "runner": {
        "is_running": False,
        "mode": "",
        "focus": "",
        "stop_at": None,
        "started_at": None,
        "last_cycle_ts": None,
        "cycles_completed": 0,
        "current_run_id": None,
        "skipped_this_run": {},
    },
    "proposals": [],
    "history": [],
    "applied_actions": {},
    "medium_cooldowns": {},
}

_state_lock = threading.RLock()
_runner_thread: Optional[threading.Thread] = None
_runner_stop_event = threading.Event()
app = FastAPI(title="Jarvis Self Evolution Guarded", version=MODULE_VERSION)

class RunOnceRequest(BaseModel):
    focus: str = "reliability"

class RunnerStartRequest(BaseModel):
    focus: str = "reliability"
    hours: float = 8.0
    mode: str = "night"

class ConfigUpdateRequest(BaseModel):
    approval_wait_seconds: Optional[int] = None
    runner_idle_sleep_seconds: Optional[int] = None
    runner_cycle_limit: Optional[int] = None
    night_default_hours: Optional[float] = None
    focus_default: Optional[str] = None
    auto_apply_low_risk: Optional[bool] = None
    require_approval_medium_risk: Optional[bool] = None
    require_approval_high_risk: Optional[bool] = None
    skip_on_timeout: Optional[bool] = None
    allowed_low_risk_actions: Optional[List[str]] = None
    allowed_medium_risk_actions: Optional[List[str]] = None
    medium_risk_cooldown_minutes: Optional[int] = None
    max_skipped_per_run_per_kind: Optional[int] = None
    max_pending_medium_total: Optional[int] = None

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def now_iso() -> str:
    return now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")

def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None

def to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default

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

def normalize_proposal(p: Dict[str, Any]) -> Dict[str, Any]:
    p.setdefault("id", f"se-unknown-{uuid.uuid4().hex[:8]}")
    p.setdefault("kind", "unknown")
    p.setdefault("focus", "reliability")
    p.setdefault("title", p["kind"])
    p.setdefault("description", "")
    p.setdefault("risk", "low")
    p.setdefault("priority", 1)
    p.setdefault("status", "queued")
    p.setdefault("created_at", now_iso())
    p.setdefault("updated_at", p["created_at"])
    p.setdefault("approval_deadline", None)
    p.setdefault("files_create", [])
    p.setdefault("files_modify", [])
    p.setdefault("council", [])
    p.setdefault("history", [])
    p.setdefault("result", None)
    return p

def compact_proposals(proposals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized = [normalize_proposal(dict(p)) for p in proposals if isinstance(p, dict)]
    normalized.sort(key=lambda x: x.get("created_at") or "", reverse=True)

    keep_pending: Dict[str, bool] = {}
    keep_queued: Dict[str, bool] = {}
    keep_applied: Dict[str, int] = {}
    keep_skipped: Dict[str, int] = {}
    keep_rejected: Dict[str, int] = {}
    compacted: List[Dict[str, Any]] = []

    for p in normalized:
        kind = str(p.get("kind") or "unknown")
        status = str(p.get("status") or "queued")

        if status == "pending_approval":
            if keep_pending.get(kind):
                continue
            keep_pending[kind] = True
            compacted.append(p)
            continue

        if status == "queued":
            if keep_queued.get(kind):
                continue
            keep_queued[kind] = True
            compacted.append(p)
            continue

        if status == "applied":
            if keep_applied.get(kind, 0) >= 1:
                continue
            keep_applied[kind] = keep_applied.get(kind, 0) + 1
            compacted.append(p)
            continue

        if status == "skipped_timeout":
            if keep_skipped.get(kind, 0) >= 1:
                continue
            keep_skipped[kind] = keep_skipped.get(kind, 0) + 1
            compacted.append(p)
            continue

        if status == "rejected":
            if keep_rejected.get(kind, 0) >= 1:
                continue
            keep_rejected[kind] = keep_rejected.get(kind, 0) + 1
            compacted.append(p)
            continue

        compacted.append(p)

    compacted.sort(key=lambda x: x.get("created_at") or "")
    return compacted

def load_state() -> Dict[str, Any]:
    data = load_json(STATE_PATH, DEFAULT_STATE)
    state = json.loads(json.dumps(DEFAULT_STATE))
    state.update(data or {})
    state.setdefault("runner", json.loads(json.dumps(DEFAULT_STATE["runner"])))
    for k, v in DEFAULT_STATE["runner"].items():
        state["runner"].setdefault(k, v)
    state.setdefault("proposals", [])
    state.setdefault("history", [])
    state.setdefault("applied_actions", {})
    state.setdefault("medium_cooldowns", {})
    state["proposals"] = compact_proposals(state["proposals"])
    return state

def save_state(state: Dict[str, Any]) -> None:
    state["proposals"] = compact_proposals(state.get("proposals", []))
    save_json(STATE_PATH, state)

def push_history(state: Dict[str, Any], event: Dict[str, Any]) -> None:
    state.setdefault("history", []).append(event)
    state["history"] = state["history"][-300:]

def proposal_status_counts(state: Dict[str, Any]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for p in state.get("proposals", []):
        status = str(p.get("status") or "queued")
        counts[status] = counts.get(status, 0) + 1
    return counts

def find_proposal(state: Dict[str, Any], proposal_id: str) -> Optional[Dict[str, Any]]:
    for p in state.get("proposals", []):
        if p.get("id") == proposal_id:
            return p
    return None

def has_recent_status(state: Dict[str, Any], kind: str, statuses: List[str]) -> bool:
    for p in reversed(state.get("proposals", [])):
        if p.get("kind") == kind and p.get("status") in statuses:
            return True
    return False

def medium_cooldown_active(state: Dict[str, Any], kind: str) -> bool:
    until = parse_iso(state.get("medium_cooldowns", {}).get(kind))
    return bool(until and until > now_utc())

def skipped_this_run_count(state: Dict[str, Any], kind: str) -> int:
    return to_int(state.get("runner", {}).get("skipped_this_run", {}).get(kind), 0)

def set_medium_cooldown(state: Dict[str, Any], kind: str, minutes: int) -> None:
    state.setdefault("medium_cooldowns", {})[kind] = (now_utc() + timedelta(minutes=max(minutes, 1))).strftime("%Y-%m-%dT%H:%M:%SZ")

def council_for(kind: str, risk: str) -> List[Dict[str, Any]]:
    title = kind.replace("_", " ").title()
    return [
        {"agent": "architect", "score": 10 if risk != "low" else 8, "verdict": "support", "note": f"Formalized concept: {title}"},
        {"agent": "reliability", "score": 11 if risk != "low" else 10, "verdict": "support", "note": "Improves observability/recovery."},
        {"agent": "safety", "score": 9 if risk != "low" else 7, "verdict": "caution" if risk != "low" else "support", "note": "Check blast radius and rollback."},
        {"agent": "implementer", "score": 9 if risk != "low" else 7, "verdict": "support", "note": "Implementation is template-based and testable."},
        {"agent": "incident_reviewer", "score": 10 if risk != "low" else 8, "verdict": "support", "note": "Recent bind/recovery signals justify hardening."},
    ]

LOW_RISK_DEFS = {
    "regression_quick_pack": {
        "title": "Regression Quick Pack",
        "description": "Adds a quick smoke/regression pack for Supervisor, Director and Bridge.",
        "files_create": ["scripts/jarvis_regression_quick_pack.ps1"],
    },
    "health_snapshot_pack": {
        "title": "Health Snapshot Pack",
        "description": "Adds a fast health/status snapshot collector for local services.",
        "files_create": ["scripts/jarvis_collect_health_snapshot.ps1"],
    },
    "backup_state_pack": {
        "title": "Backup Core State Pack",
        "description": "Adds a safe backup pack for configs, logs and service state.",
        "files_create": ["scripts/jarvis_backup_core_state.ps1"],
    },
    "log_summary_pack": {
        "title": "Log Summary Pack",
        "description": "Adds an automatic log summary utility for errors and warnings.",
        "files_create": ["scripts/jarvis_log_summary.ps1"],
    },
    "inventory_snapshot_pack": {
        "title": "Inventory Snapshot Pack",
        "description": "Adds a workflow inventory snapshot collector for Director state.",
        "files_create": ["scripts/jarvis_inventory_snapshot.ps1"],
    },
    "cleanup_audit_pack": {
        "title": "Cleanup Audit Pack",
        "description": "Adds a cleanup audit/report script without modifying workflows.",
        "files_create": ["scripts/jarvis_cleanup_audit.ps1"],
    },
    "proposal_digest_pack": {
        "title": "Proposal Digest Pack",
        "description": "Adds a digest generator for self-evolution proposals and statuses.",
        "files_create": ["scripts/jarvis_self_evolution_digest.ps1"],
    },
    "runner_watchdog_pack": {
        "title": "Runner Watchdog Pack",
        "description": "Adds a watchdog/status check helper for the self-evolution runner.",
        "files_create": ["scripts/jarvis_self_evolution_watchdog.ps1"],
    },
}

MEDIUM_RISK_DEFS = {
    "director_lifecycle_tuneup": {
        "title": "Director Lifecycle Tuneup",
        "description": "Plan-only medium-risk hardening for Director lifecycle/cleanup policy.",
        "files_create": ["jarvis_stage3_artifacts/self_evolution/proposed_patches/director_lifecycle_tuneup_plan.md"],
        "files_modify": ["app/jarvis_local_n8n_director_pro_core.py"],
    },
    "supervisor_router_hardening": {
        "title": "Supervisor Router Hardening",
        "description": "Plan-only medium-risk hardening for supervisor router recovery and hosting.",
        "files_create": ["jarvis_stage3_artifacts/self_evolution/proposed_patches/supervisor_router_hardening_plan.md"],
        "files_modify": ["app/main.py"],
    },
}

def ps_text(title: str, body: str) -> str:
    return f"# {title}\nSet-StrictMode -Version Latest\n$ErrorActionPreference = 'Stop'\n\n{body}\n"

def create_script(path_rel: str, content: str) -> str:
    path = PROJECT_ROOT / path_rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(path)

def write_low_risk_script(kind: str, config: Dict[str, Any]) -> Dict[str, Any]:
    sup = config["supervisor_base_url"]
    director = config["director_base_url"]
    bridge = config["bridge_base_url"]

    if kind == "regression_quick_pack":
        created = create_script(
            "scripts/jarvis_regression_quick_pack.ps1",
            ps_text(
                "Jarvis Regression Quick Pack",
                f'''$OutDir = Join-Path "{PROJECT_ROOT}" "jarvis_stage3_artifacts\\self_evolution\\regression_runs"
if (-not (Test-Path $OutDir)) {{ New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }}
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$Out = Join-Path $OutDir "regression_$Stamp.json"
$Result = [ordered]@{{
    ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    supervisor = Invoke-RestMethod -Method GET -Uri "{sup}/health" -TimeoutSec 15
    director = Invoke-RestMethod -Method GET -Uri "{director}/health" -TimeoutSec 15
    bridge = Invoke-RestMethod -Method GET -Uri "{bridge}/health" -TimeoutSec 15
}}
$Result | ConvertTo-Json -Depth 40 | Set-Content -Encoding UTF8 -Path $Out
$Result | ConvertTo-Json -Depth 40'''
            ),
        )
        return {"created_file": created}

    if kind == "health_snapshot_pack":
        created = create_script(
            "scripts/jarvis_collect_health_snapshot.ps1",
            ps_text(
                "Jarvis Health Snapshot Pack",
                f'''$OutDir = Join-Path "{PROJECT_ROOT}" "jarvis_stage3_artifacts\\self_evolution\\health_snapshots"
if (-not (Test-Path $OutDir)) {{ New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }}
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$Out = Join-Path $OutDir "health_$Stamp.json"
$Result = [ordered]@{{
    ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    supervisor = Invoke-RestMethod -Method GET -Uri "{sup}/health" -TimeoutSec 15
    director = Invoke-RestMethod -Method GET -Uri "{director}/api/jarvis/n8n/status/all?limit=50" -TimeoutSec 30
    bridge = Invoke-RestMethod -Method GET -Uri "{bridge}/health" -TimeoutSec 15
}}
$Result | ConvertTo-Json -Depth 80 | Set-Content -Encoding UTF8 -Path $Out
$Result | ConvertTo-Json -Depth 80'''
            ),
        )
        return {"created_file": created}

    if kind == "backup_state_pack":
        created = create_script(
            "scripts/jarvis_backup_core_state.ps1",
            ps_text(
                "Jarvis Backup Core State Pack",
                f'''$OutDir = Join-Path "{PROJECT_ROOT}" "jarvis_stage3_artifacts\\self_evolution\\backups\\$(Get-Date -Format yyyyMMdd_HHmmss)"
New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
$Paths = @(
    (Join-Path "{PROJECT_ROOT}" ".env"),
    (Join-Path "{PROJECT_ROOT}" "jarvis_stage3_artifacts\\logs"),
    (Join-Path "{PROJECT_ROOT}" "jarvis_stage3_artifacts\\self_evolution"),
    (Join-Path "{PROJECT_ROOT}" "jarvis_stage3_artifacts\\n8n_templates")
)
foreach ($P in $Paths) {{
    if (Test-Path $P) {{
        $Name = Split-Path $P -Leaf
        Copy-Item -Path $P -Destination (Join-Path $OutDir $Name) -Recurse -Force -ErrorAction SilentlyContinue
    }}
}}
Write-Host $OutDir -ForegroundColor Cyan'''
            ),
        )
        return {"created_file": created}

    if kind == "log_summary_pack":
        created = create_script(
            "scripts/jarvis_log_summary.ps1",
            ps_text(
                "Jarvis Log Summary Pack",
                f'''$LogsDir = Join-Path "{PROJECT_ROOT}" "jarvis_stage3_artifacts\\logs"
$OutDir = Join-Path "{PROJECT_ROOT}" "jarvis_stage3_artifacts\\self_evolution\\log_summaries"
if (-not (Test-Path $OutDir)) {{ New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }}
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$Out = Join-Path $OutDir "log_summary_$Stamp.json"
$Patterns = @("bind_error","Traceback","ERROR","Exception","warning","WARNING")
$Rows = @()
Get-ChildItem $LogsDir -File -ErrorAction SilentlyContinue | ForEach-Object {{
    $Text = Get-Content $_.FullName -Tail 300 -ErrorAction SilentlyContinue
    $Counts = @{{}}
    foreach ($Pat in $Patterns) {{
        $Counts[$Pat] = @($Text | Select-String -Pattern $Pat -SimpleMatch).Count
    }}
    $Rows += [pscustomobject]@{{ path = $_.FullName; counts = $Counts }}
}}
$Rows | ConvertTo-Json -Depth 40 | Set-Content -Encoding UTF8 -Path $Out
$Rows | ConvertTo-Json -Depth 40'''
            ),
        )
        return {"created_file": created}

    if kind == "inventory_snapshot_pack":
        created = create_script(
            "scripts/jarvis_inventory_snapshot.ps1",
            ps_text(
                "Jarvis Inventory Snapshot Pack",
                f'''$OutDir = Join-Path "{PROJECT_ROOT}" "jarvis_stage3_artifacts\\self_evolution\\inventory_snapshots"
if (-not (Test-Path $OutDir)) {{ New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }}
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$Out = Join-Path $OutDir "inventory_$Stamp.json"
$Resp = Invoke-RestMethod -Method GET -Uri "{director}/api/jarvis/n8n/workflows/inventory?limit=200" -TimeoutSec 30
$Resp | ConvertTo-Json -Depth 80 | Set-Content -Encoding UTF8 -Path $Out
$Resp | ConvertTo-Json -Depth 80'''
            ),
        )
        return {"created_file": created}

    if kind == "cleanup_audit_pack":
        created = create_script(
            "scripts/jarvis_cleanup_audit.ps1",
            ps_text(
                "Jarvis Cleanup Audit Pack",
                f'''$OutDir = Join-Path "{PROJECT_ROOT}" "jarvis_stage3_artifacts\\self_evolution\\cleanup_audits"
if (-not (Test-Path $OutDir)) {{ New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }}
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$Out = Join-Path $OutDir "cleanup_$Stamp.json"
$Resp = Invoke-RestMethod -Method GET -Uri "{director}/api/jarvis/n8n/lifecycle/cleanup-report?older_than_hours=0&max_keep_per_template=3&limit=200" -TimeoutSec 30
$Resp | ConvertTo-Json -Depth 80 | Set-Content -Encoding UTF8 -Path $Out
$Resp | ConvertTo-Json -Depth 80'''
            ),
        )
        return {"created_file": created}

    if kind == "proposal_digest_pack":
        created = create_script(
            "scripts/jarvis_self_evolution_digest.ps1",
            ps_text(
                "Jarvis Self Evolution Digest Pack",
                '''$Base = "http://127.0.0.1:8028"
$Queue = Invoke-RestMethod -Method GET -Uri "$Base/api/self-evolution/proposals" -TimeoutSec 30
$Status = Invoke-RestMethod -Method GET -Uri "$Base/api/self-evolution/status" -TimeoutSec 30
[ordered]@{ queue = $Queue; status = $Status } | ConvertTo-Json -Depth 100'''
            ),
        )
        return {"created_file": created}

    if kind == "runner_watchdog_pack":
        created = create_script(
            "scripts/jarvis_self_evolution_watchdog.ps1",
            ps_text(
                "Jarvis Self Evolution Watchdog Pack",
                '''$Base = "http://127.0.0.1:8028"
$Status = Invoke-RestMethod -Method GET -Uri "$Base/api/self-evolution/status" -TimeoutSec 30
if (-not $Status.runner.is_running) {
    Write-Host "runner_not_running" -ForegroundColor Yellow
} else {
    Write-Host "runner_running" -ForegroundColor Green
}
$Status | ConvertTo-Json -Depth 80'''
            ),
        )
        return {"created_file": created}

    raise RuntimeError(f"Unsupported low-risk kind: {kind}")

def write_medium_patch_plan(kind: str, signals: Dict[str, Any]) -> Dict[str, Any]:
    if kind not in MEDIUM_RISK_DEFS:
        raise RuntimeError(f"Unsupported medium-risk kind: {kind}")
    info = MEDIUM_RISK_DEFS[kind]
    plan_path = PROJECT_ROOT / info["files_create"][0]
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join([
        f"# {info['title']}",
        "",
        f"- Generated at: {now_iso()}",
        f"- Kind: {kind}",
        "- Mode: plan_only",
        "- Reason: repeated bind/recovery/log signals or reliability focus",
        "",
        "## Candidate files",
        *[f"- {p}" for p in info.get("files_modify", [])],
        "",
        "## Observed signals",
        f"- log_patterns: {json.dumps(((signals.get('logs') or {}).get('patterns') or {}), ensure_ascii=False)}",
        f"- log_error_lines: {((signals.get('logs') or {}).get('error_lines') or 0)}",
        "",
        "## Safe implementation constraints",
        "- keep restart/port guard logic idempotent",
        "- avoid breaking existing endpoints",
        "- prefer additive lifecycle/reporting changes",
        "- require compile + health + smoke verification before merge",
        "",
        "## Recommended next manual step",
        "- review this plan and approve an implementation block explicitly",
        "",
    ])
    plan_path.write_text(text, encoding="utf-8")
    return {"plan_file": str(plan_path), "requires_manual_merge": True}

def fetch_json(url: str, timeout: int = 15) -> Dict[str, Any]:
    try:
        resp = requests.get(url, timeout=timeout)
        try:
            body = resp.json()
        except Exception:
            body = resp.text
        return {"ok": 200 <= resp.status_code < 300, "status_code": resp.status_code, "body": body, "url": url}
    except Exception as exc:
        return {"ok": False, "status_code": 0, "body": str(exc), "url": url}

def scan_logs() -> Dict[str, Any]:
    patterns = {
        "bind_error": ["bind", "10048", "address already in use"],
        "traceback": ["Traceback"],
        "timeout": ["timeout", "timed out"],
        "exception": ["Exception", "ERROR"],
    }
    files: List[Dict[str, Any]] = []
    error_lines = 0
    warning_lines = 0
    pattern_totals = {k: 0 for k in patterns}

    for path in sorted(LOGS_DIR.glob("*.log")):
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()[-MAX_LOG_TAIL_LINES:]
        except Exception:
            lines = []

        text_lower = "\n".join(lines).lower()
        file_patterns: Dict[str, int] = {}
        errs = 0
        warns = 0
        for line in lines:
            ll = line.lower()
            if "error" in ll or "traceback" in ll or "exception" in ll:
                errs += 1
            if "warning" in ll:
                warns += 1
        for key, pats in patterns.items():
            count = 0
            for p in pats:
                count += text_lower.count(p.lower())
            if count:
                file_patterns[key] = count
                pattern_totals[key] += count

        if errs or warns or file_patterns:
            files.append({
                "path": str(path),
                "errors": errs,
                "warnings": warns,
                "matches": file_patterns,
            })
        error_lines += errs
        warning_lines += warns

    return {
        "files": files,
        "error_lines": error_lines,
        "warning_lines": warning_lines,
        "patterns": {k: v for k, v in pattern_totals.items() if v},
    }

def gather_signals(config: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "supervisor": {"health": fetch_json(f"{config['supervisor_base_url']}/health", timeout=10)},
        "director": {
            "health": fetch_json(f"{config['director_base_url']}/health", timeout=10),
            "inventory": fetch_json(f"{config['director_base_url']}/api/jarvis/n8n/workflows/inventory?limit=200", timeout=25),
            "cleanup": fetch_json(f"{config['director_base_url']}/api/jarvis/n8n/lifecycle/cleanup-report?older_than_hours=0&max_keep_per_template=3&limit=200", timeout=25),
        },
        "bridge": {"health": fetch_json(f"{config['bridge_base_url']}/health", timeout=10)},
        "logs": scan_logs(),
        "ts": now_iso(),
    }

def can_create_medium(state: Dict[str, Any], config: Dict[str, Any], kind: str) -> bool:
    if kind not in config.get("allowed_medium_risk_actions", []):
        return False
    if medium_cooldown_active(state, kind):
        return False
    if skipped_this_run_count(state, kind) >= to_int(config.get("max_skipped_per_run_per_kind"), 1):
        return False
    if has_recent_status(state, kind, ["pending_approval", "queued"]):
        return False
    return True

def enqueue_if_needed(state: Dict[str, Any], proposal: Dict[str, Any]) -> None:
    if has_recent_status(state, proposal["kind"], ["queued", "pending_approval", "applied"]):
        return
    state.setdefault("proposals", []).append(proposal)

def new_proposal(kind: str, focus: str, risk: str, priority: int, title: str, description: str, files_create: List[str], files_modify: List[str]) -> Dict[str, Any]:
    created = now_iso()
    return {
        "id": f"se-{kind}-{uuid.uuid4().hex[:8]}",
        "kind": kind,
        "focus": focus,
        "title": title,
        "description": description,
        "risk": risk,
        "priority": priority,
        "status": "queued",
        "created_at": created,
        "updated_at": created,
        "approval_deadline": None,
        "files_create": files_create,
        "files_modify": files_modify,
        "council": council_for(kind, risk),
        "history": [{"ts": created, "event": "created"}],
        "result": None,
    }

def build_candidate_proposals(state: Dict[str, Any], config: Dict[str, Any], focus: str, signals: Dict[str, Any]) -> None:
    for kind in config.get("allowed_low_risk_actions", []):
        if kind not in LOW_RISK_DEFS:
            continue
        if state.get("applied_actions", {}).get(kind):
            continue
        if has_recent_status(state, kind, ["queued", "pending_approval", "applied"]):
            continue
        info = LOW_RISK_DEFS[kind]
        enqueue_if_needed(state, new_proposal(kind, focus, "low", 7, info["title"], info["description"], info["files_create"], []))

    log_patterns = ((signals.get("logs") or {}).get("patterns") or {})
    should_harden = bool(log_patterns.get("bind_error") or log_patterns.get("traceback") or focus == "reliability")
    if not should_harden:
        return

    pending_medium_total = sum(1 for p in state.get("proposals", []) if p.get("risk") == "medium" and p.get("status") == "pending_approval")
    if pending_medium_total >= to_int(config.get("max_pending_medium_total"), 1):
        return

    for kind in config.get("allowed_medium_risk_actions", []):
        if kind not in MEDIUM_RISK_DEFS:
            continue
        if not can_create_medium(state, config, kind):
            continue
        info = MEDIUM_RISK_DEFS[kind]
        enqueue_if_needed(state, new_proposal(kind, focus, "medium", 9, info["title"], info["description"], info["files_create"], info["files_modify"]))

def mark_timeout_skips(state: Dict[str, Any], config: Dict[str, Any]) -> None:
    if not config.get("skip_on_timeout", True):
        return
    for p in state.get("proposals", []):
        if p.get("status") != "pending_approval":
            continue
        deadline = parse_iso(p.get("approval_deadline"))
        if deadline and deadline <= now_utc():
            p["status"] = "skipped_timeout"
            p["updated_at"] = now_iso()
            p.setdefault("history", []).append({"ts": p["updated_at"], "event": "skipped_timeout"})
            kind = str(p.get("kind"))
            state.setdefault("runner", {}).setdefault("skipped_this_run", {})
            state["runner"]["skipped_this_run"][kind] = skipped_this_run_count(state, kind) + 1
            set_medium_cooldown(state, kind, to_int(config.get("medium_risk_cooldown_minutes"), 240))
            push_history(state, {"ts": p["updated_at"], "proposal_id": p["id"], "event": "skipped_timeout"})

def promote_medium_for_approval(state: Dict[str, Any], config: Dict[str, Any]) -> None:
    if any(p.get("status") == "pending_approval" for p in state.get("proposals", [])):
        return
    queued_medium = [p for p in state.get("proposals", []) if p.get("risk") == "medium" and p.get("status") == "queued"]
    if not queued_medium:
        return
    queued_medium.sort(key=lambda x: (-to_int(x.get("priority"), 0), x.get("created_at") or ""))
    chosen = queued_medium[0]
    chosen["status"] = "pending_approval"
    chosen["updated_at"] = now_iso()
    chosen["approval_deadline"] = (now_utc() + timedelta(seconds=to_int(config.get("approval_wait_seconds"), 600))).strftime("%Y-%m-%dT%H:%M:%SZ")
    chosen.setdefault("history", []).append({"ts": chosen["updated_at"], "event": "pending_approval"})

def apply_low_risk_proposal(state: Dict[str, Any], config: Dict[str, Any], proposal: Dict[str, Any]) -> None:
    result = write_low_risk_script(str(proposal["kind"]), config)
    proposal["status"] = "applied"
    proposal["updated_at"] = now_iso()
    proposal["result"] = result
    proposal.setdefault("history", []).append({"ts": proposal["updated_at"], "event": "applied", "result": result})
    state.setdefault("applied_actions", {})[proposal["kind"]] = {"ts": proposal["updated_at"], "proposal_id": proposal["id"], "risk": proposal["risk"]}
    push_history(state, {"ts": proposal["updated_at"], "proposal_id": proposal["id"], "event": "applied"})

def apply_approved_medium(state: Dict[str, Any], proposal: Dict[str, Any], signals: Dict[str, Any]) -> None:
    result = write_medium_patch_plan(str(proposal["kind"]), signals)
    proposal["status"] = "applied"
    proposal["updated_at"] = now_iso()
    proposal["result"] = result
    proposal.setdefault("history", []).append({"ts": proposal["updated_at"], "event": "applied", "result": result})
    state.setdefault("applied_actions", {})[proposal["kind"]] = {"ts": proposal["updated_at"], "proposal_id": proposal["id"], "risk": proposal["risk"], "mode": "plan_only"}
    push_history(state, {"ts": proposal["updated_at"], "proposal_id": proposal["id"], "event": "applied_plan_only"})

def auto_apply_low_risk_queue(state: Dict[str, Any], config: Dict[str, Any]) -> None:
    if not config.get("auto_apply_low_risk", True):
        return
    allowed = set(config.get("allowed_low_risk_actions", []))
    queued = [p for p in state.get("proposals", []) if p.get("risk") == "low" and p.get("status") == "queued" and p.get("kind") in allowed]
    queued.sort(key=lambda x: (-to_int(x.get("priority"), 0), x.get("created_at") or ""))
    for p in queued:
        apply_low_risk_proposal(state, config, p)

def summarize(state: Dict[str, Any], signals: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ts": now_iso(),
        "signals": signals,
        "proposals": state.get("proposals", []),
        "history": state.get("history", [])[-100:],
        "applied_actions": state.get("applied_actions", {}),
        "status_counts": proposal_status_counts(state),
    }

def persist_report(report: Dict[str, Any]) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = now_utc().strftime("%Y%m%d_%H%M%S")
    path = REPORTS_DIR / f"self_evolution_report_{stamp}.json"
    save_json(path, report)
    save_json(LATEST_REPORT_PATH, report)

def run_cycle(focus: str) -> Dict[str, Any]:
    with _state_lock:
        config = load_config()
        state = load_state()
        signals = gather_signals(config)
        mark_timeout_skips(state, config)
        build_candidate_proposals(state, config, focus, signals)
        auto_apply_low_risk_queue(state, config)
        promote_medium_for_approval(state, config)
        state["runner"]["last_cycle_ts"] = now_iso()
        state["runner"]["cycles_completed"] = to_int(state["runner"].get("cycles_completed"), 0) + 1
        report = summarize(state, signals)
        save_state(state)
        persist_report(report)
        return report

def runner_loop() -> None:
    while not _runner_stop_event.is_set():
        with _state_lock:
            state = load_state()
            config = load_config()
            runner = state.get("runner", {})
            stop_at = parse_iso(runner.get("stop_at"))
            if not runner.get("is_running"):
                save_state(state)
                return
            if stop_at and stop_at <= now_utc():
                runner["is_running"] = False
                runner["last_cycle_ts"] = now_iso()
                save_state(state)
                return
            focus = str(runner.get("focus") or config.get("focus_default") or "reliability")

        try:
            run_cycle(focus)
        except Exception as exc:
            with _state_lock:
                state = load_state()
                push_history(state, {"ts": now_iso(), "event": "runner_exception", "error": str(exc)})
                save_state(state)

        with _state_lock:
            state = load_state()
            config = load_config()
            if not state.get("runner", {}).get("is_running"):
                save_state(state)
                return
            cycle_limit = to_int(config.get("runner_cycle_limit"), 0)
            completed = to_int(state.get("runner", {}).get("cycles_completed"), 0)
            if cycle_limit > 0 and completed >= cycle_limit:
                state["runner"]["is_running"] = False
                state["runner"]["last_cycle_ts"] = now_iso()
                save_state(state)
                return
            save_state(state)

        if _runner_stop_event.wait(timeout=max(to_int(config.get("runner_idle_sleep_seconds"), 20), 5)):
            break

def ensure_runner_started() -> None:
    global _runner_thread
    if _runner_thread is not None and _runner_thread.is_alive():
        return
    _runner_stop_event.clear()
    _runner_thread = threading.Thread(target=runner_loop, name="jarvis-self-evolution-runner", daemon=True)
    _runner_thread.start()

@app.get("/health")
def health() -> Dict[str, Any]:
    state = load_state()
    return {
        "status": "healthy",
        "service": MODULE_ID,
        "version": MODULE_VERSION,
        "runner_running": bool((state.get("runner") or {}).get("is_running")),
        "proposals": len(state.get("proposals", [])),
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

@app.get("/api/self-evolution/config")
def get_config() -> Dict[str, Any]:
    return {"status": "ok", "service": MODULE_ID, "config": load_config()}

@app.post("/api/self-evolution/config/update")
def update_config(req: ConfigUpdateRequest) -> Dict[str, Any]:
    config = load_config()
    update = req.model_dump(exclude_none=True)
    for k, v in update.items():
        config[k] = v
    save_config(config)
    return {"status": "ok", "service": MODULE_ID, "config": config}

@app.get("/api/self-evolution/status")
def status() -> Dict[str, Any]:
    state = load_state()
    config = load_config()
    return {
        "status": "ok",
        "service": MODULE_ID,
        "runner": state.get("runner", {}),
        "proposal_counts": proposal_status_counts(state),
        "history": state.get("history", [])[-50:],
        "applied_actions": state.get("applied_actions", {}),
        "config": config,
    }

@app.post("/api/self-evolution/run/once")
def run_once(req: RunOnceRequest) -> Dict[str, Any]:
    report = run_cycle(req.focus or "reliability")
    return {"status": "ok", "service": MODULE_ID, "report": report}

@app.post("/api/self-evolution/runner/start")
def runner_start(req: RunnerStartRequest) -> Dict[str, Any]:
    with _state_lock:
        state = load_state()
        state["runner"] = {
            "is_running": True,
            "mode": req.mode or "night",
            "focus": req.focus or "reliability",
            "stop_at": (now_utc() + timedelta(hours=max(req.hours, 0.25))).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "started_at": now_iso(),
            "last_cycle_ts": None,
            "cycles_completed": 0,
            "current_run_id": uuid.uuid4().hex[:12],
            "skipped_this_run": {},
        }
        save_state(state)
    ensure_runner_started()
    return {"status": "ok", "service": MODULE_ID, "message": "runner_started", "stop_at": load_state()["runner"]["stop_at"]}

@app.post("/api/self-evolution/runner/stop")
def runner_stop() -> Dict[str, Any]:
    with _state_lock:
        state = load_state()
        state["runner"]["is_running"] = False
        state["runner"]["last_cycle_ts"] = now_iso()
        save_state(state)
    _runner_stop_event.set()
    return {"status": "ok", "service": MODULE_ID, "message": "runner_stop_requested"}

@app.get("/api/self-evolution/proposals")
def proposals() -> Dict[str, Any]:
    state = load_state()
    return {"status": "ok", "service": MODULE_ID, "proposals": state.get("proposals", [])}

@app.post("/api/self-evolution/proposals/{proposal_id}/approve")
def approve(proposal_id: str) -> Dict[str, Any]:
    with _state_lock:
        state = load_state()
        config = load_config()
        p = find_proposal(state, proposal_id)
        if not p:
            raise HTTPException(status_code=404, detail="Proposal not found")
        if p.get("risk") == "low":
            apply_low_risk_proposal(state, config, p)
        else:
            signals = gather_signals(config)
            apply_approved_medium(state, p, signals)
        save_state(state)
        report = summarize(state, gather_signals(config))
        persist_report(report)
        return {"status": "ok", "service": MODULE_ID, "proposal": p}

@app.post("/api/self-evolution/proposals/{proposal_id}/reject")
def reject(proposal_id: str) -> Dict[str, Any]:
    with _state_lock:
        state = load_state()
        p = find_proposal(state, proposal_id)
        if not p:
            raise HTTPException(status_code=404, detail="Proposal not found")
        p["status"] = "rejected"
        p["updated_at"] = now_iso()
        p.setdefault("history", []).append({"ts": p["updated_at"], "event": "rejected"})
        push_history(state, {"ts": p["updated_at"], "proposal_id": p["id"], "event": "rejected"})
        save_state(state)
        return {"status": "ok", "service": MODULE_ID, "proposal": p}

@app.get("/api/self-evolution/reports/latest")
def reports_latest() -> Dict[str, Any]:
    if not LATEST_REPORT_PATH.exists():
        raise HTTPException(status_code=404, detail="Latest report not found")
    return json.loads(LATEST_REPORT_PATH.read_text(encoding="utf-8"))

@app.on_event("startup")
def startup() -> None:
    cfg = load_config()
    save_config(cfg)
    st = load_state()
    save_state(st)