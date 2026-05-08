
from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

OPTIONAL_ENDPOINTS = {
    "/api/autonomy/health",
    "/api/spreadsheets/health",
}

PROBE_ENDPOINTS = [
    ("/health", "backend_health"),
    ("/api/control/health", "control_health"),
    ("/api/ai/health", "ai_health"),
    ("/api/autonomy/health", "autonomy_health"),
    ("/api/agents/adapters", "agents_adapters"),
    ("/api/agents/registry", "agents_registry"),
    ("/api/spreadsheets/health", "spreadsheets_health"),
]

PANEL_CHAT_MEMORY_FILE = "panel_chat_memory.json"
PANEL_UI_STATE_FILE = "panel_ui_state_v4.json"


def read_text_best_effort(path: Path) -> str:
    if not path.exists():
        return ""
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "cp1251", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except Exception:
            pass
    return raw.decode("utf-8", errors="replace")


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def panel_memory_path(project_root: Path) -> Path:
    return project_root / "jarvis_stage3_artifacts" / PANEL_CHAT_MEMORY_FILE


def ui_state_path(project_root: Path) -> Path:
    return project_root / "jarvis_stage3_artifacts" / PANEL_UI_STATE_FILE


def load_json_file(path: Path, default):
    if not path.exists():
        return default
    try:
        text = read_text_best_effort(path)
        if not text.strip():
            return default
        value = json.loads(text)
        return value
    except Exception:
        return default


def save_json_file(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_panel_memory(project_root: Path) -> dict:
    data = load_json_file(panel_memory_path(project_root), {"items": []})
    if not isinstance(data, dict):
        data = {"items": []}
    if not isinstance(data.get("items"), list):
        data["items"] = []
    return data


def save_panel_memory(project_root: Path, data: dict) -> None:
    save_json_file(panel_memory_path(project_root), data)


def add_panel_memory(project_root: Path, text: str) -> dict:
    data = load_panel_memory(project_root)
    items = data.get("items", [])
    items.append({
        "text": text,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    data["items"] = items[-300:]
    save_panel_memory(project_root, data)
    return data


def load_ui_state(project_root: Path) -> dict:
    data = load_json_file(ui_state_path(project_root), {
        "messages": [],
        "last_action_result": "",
        "last_task_result": "",
        "last_error": "",
        "updated_at": "",
    })
    if not isinstance(data, dict):
        data = {}
    if not isinstance(data.get("messages"), list):
        data["messages"] = []
    data.setdefault("last_action_result", "")
    data.setdefault("last_task_result", "")
    data.setdefault("last_error", "")
    data.setdefault("updated_at", "")
    return data


def save_ui_state(project_root: Path, state: dict) -> None:
    state["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save_json_file(ui_state_path(project_root), state)


def add_ui_message(project_root: Path, role: str, text: str) -> dict:
    state = load_ui_state(project_root)
    messages = state.get("messages", [])
    messages.append({
        "role": role,
        "text": text,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    state["messages"] = messages[-80:]
    save_ui_state(project_root, state)
    return state


def set_last_action_result(project_root: Path, text: str) -> dict:
    state = load_ui_state(project_root)
    state["last_action_result"] = text
    save_ui_state(project_root, state)
    return state


def set_last_task_result(project_root: Path, text: str) -> dict:
    state = load_ui_state(project_root)
    state["last_task_result"] = text
    save_ui_state(project_root, state)
    return state


def set_last_error(project_root: Path, text: str) -> dict:
    state = load_ui_state(project_root)
    state["last_error"] = text
    save_ui_state(project_root, state)
    return state


def clear_chat_state(project_root: Path) -> dict:
    state = load_ui_state(project_root)
    state["messages"] = []
    save_ui_state(project_root, state)
    return state


def extract_remember_text(message: str) -> str:
    msg = message.strip()
    lower = msg.lower()
    for marker in ("запомни:", "remember:"):
        idx = lower.find(marker)
        if idx != -1:
            tail = msg[idx + len(marker):].strip(" :,-")
            if tail:
                return tail
    for marker in ("запомни это", "remember this"):
        idx = lower.find(marker)
        if idx != -1:
            before = msg[:idx].strip(" ,:-")
            after = msg[idx + len(marker):].strip(" ,:-")
            if after:
                return after
            if before:
                before = re.sub(r"^(привет|hello|hi)\s*,?\s*", "", before, flags=re.I).strip(" ,:-")
                if before:
                    return before
    for marker in ("запомни", "remember"):
        idx = lower.find(marker)
        if idx != -1:
            tail = msg[idx + len(marker):].strip(" :,-")
            if tail:
                return tail
    return msg


def _extract_items(data):
    if isinstance(data, dict):
        for key in ("items", "agents", "data", "results"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    if isinstance(data, list):
        return data
    return []


def safe_probe(base_url: str, path: str, name: str) -> dict:
    url = base_url.rstrip("/") + path
    started = time.time()
    try:
        req = Request(url, headers={"User-Agent": "JarvisOperatorPanel/4.0"})
        with urlopen(req, timeout=8) as resp:
            body = resp.read()
            elapsed_ms = int((time.time() - started) * 1000)
            text = body.decode("utf-8", errors="replace")
            summary = ""
            try:
                data = json.loads(text)
                if isinstance(data, dict):
                    if "status" in data:
                        summary = str(data.get("status"))
                    elif "service" in data:
                        summary = str(data.get("service"))
                    elif "ok" in data:
                        summary = "ok=" + str(data.get("ok"))
                    else:
                        summary = "json"
                else:
                    summary = type(data).__name__
            except Exception:
                summary = text[:160]
            return {
                "name": name,
                "path": path,
                "ok": True,
                "level": "ok",
                "status_code": getattr(resp, "status", 200),
                "status_text": "ok",
                "elapsed_ms": elapsed_ms,
                "summary": summary,
                "error": "",
            }
    except HTTPError as e:
        level = "optional_missing" if (e.code == 404 and path in OPTIONAL_ENDPOINTS) else "bad"
        status_text = "optional_404" if level == "optional_missing" else f"http_{e.code}"
        return {
            "name": name,
            "path": path,
            "ok": False,
            "level": level,
            "status_code": e.code,
            "status_text": status_text,
            "elapsed_ms": int((time.time() - started) * 1000),
            "summary": "",
            "error": str(e),
        }
    except URLError as e:
        return {
            "name": name,
            "path": path,
            "ok": False,
            "level": "bad",
            "status_code": None,
            "status_text": "unreachable",
            "elapsed_ms": int((time.time() - started) * 1000),
            "summary": "",
            "error": str(e),
        }
    except Exception as e:
        return {
            "name": name,
            "path": path,
            "ok": False,
            "level": "bad",
            "status_code": None,
            "status_text": "error",
            "elapsed_ms": int((time.time() - started) * 1000),
            "summary": "",
            "error": str(e),
        }


def build_probe_summary(items: list[dict]) -> dict:
    ok_count = sum(1 for x in items if x["level"] == "ok")
    optional_missing_count = sum(1 for x in items if x["level"] == "optional_missing")
    bad_count = sum(1 for x in items if x["level"] == "bad")
    critical_ok = all((x["level"] == "ok") or (x["path"] in OPTIONAL_ENDPOINTS) for x in items)
    return {
        "critical_ok": critical_ok,
        "ok_count": ok_count,
        "optional_missing_count": optional_missing_count,
        "bad_count": bad_count,
    }


def list_real_runs(project_root: Path, limit: int = 12) -> list[dict]:
    runs_dir = project_root / "jarvis_stage3_artifacts" / "real_runs"
    if not runs_dir.exists():
        return []
    dirs = [p for p in runs_dir.iterdir() if p.is_dir()]
    dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    items = []
    for run_dir in dirs[:limit]:
        files = []
        for child in sorted(run_dir.iterdir(), key=lambda p: p.name.lower()):
            if child.is_file():
                files.append({"name": child.name, "size": child.stat().st_size})
        items.append({
            "name": run_dir.name,
            "path": str(run_dir),
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(run_dir.stat().st_mtime)),
            "files": files[:12],
        })
    return items


def find_latest_run_dir(project_root: Path) -> Path | None:
    runs = list_real_runs(project_root, limit=1)
    if not runs:
        return None
    return Path(runs[0]["path"])


def summarize_runs(project_root: Path) -> str:
    runs = list_real_runs(project_root, limit=3)
    if not runs:
        return "Пока не вижу доступных real runs."
    lines = ["Вот самые свежие real runs:"]
    for run in runs:
        names = ", ".join(x["name"] for x in run.get("files", [])[:6]) or "без файлов"
        lines.append(f"- {run['name']} — обновлён {run['updated_at']}; файлы: {names}")
    return "\n".join(lines)


def hardening_report(project_root: Path) -> dict:
    scripts_dir = project_root / "scripts"
    helper = read_text_best_effort(scripts_dir / "jarvis_real_run_helpers.ps1")
    run_real = read_text_best_effort(scripts_dir / "jarvis_run_real_sop_package.ps1")
    phase2 = read_text_best_effort(scripts_dir / "jarvis_sop_phase2_finalize.ps1")
    normalize_py = scripts_dir / "jarvis_artifact_normalize_utf8.py"
    checks = {
        "helper_exists": (scripts_dir / "jarvis_real_run_helpers.ps1").exists(),
        "run_real_exists": (scripts_dir / "jarvis_run_real_sop_package.ps1").exists(),
        "phase2_exists": (scripts_dir / "jarvis_sop_phase2_finalize.ps1").exists(),
        "normalize_py_exists": normalize_py.exists(),
        "helper_has_approval_guard": "function Assert-AdapterResponseApproved" in helper,
        "helper_has_governed_request": "function Invoke-GovernedJsonRequest" in helper,
        "helper_has_phase2_finalize": "function Invoke-SopPhase2Finalize" in helper,
        "helper_has_normalize_run_dir": "function Normalize-ArtifactRunDir" in helper,
        "run_real_uses_governed_request": "Invoke-GovernedJsonRequest" in run_real,
        "run_real_prompt_hardened": "Return ONLY the final complete operator-ready SOP document in markdown for this topic:" in run_real,
        "phase2_references_normalizer": "jarvis_artifact_normalize_utf8.py" in phase2,
        "panel_server_v4_exists": True,
    }
    return {"checks": checks}


def run_powershell_file(project_root: Path, script_name: str, extra_args: list[str] | None = None, timeout: int = 240) -> dict:
    script_path = project_root / "scripts" / script_name
    if not script_path.exists():
        return {"ok": False, "error": f"script not found: {script_path}"}
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_path),
    ]
    if extra_args:
        cmd.extend(extra_args)
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(project_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout[-16000:],
            "stderr": proc.stderr[-16000:],
            "script": str(script_path),
        }
    except subprocess.TimeoutExpired as e:
        return {
            "ok": False,
            "returncode": None,
            "stdout": (e.stdout or "")[-16000:] if isinstance(e.stdout, str) else "",
            "stderr": (e.stderr or "")[-16000:] if isinstance(e.stderr, str) else "",
            "error": f"timeout after {timeout}s",
            "script": str(script_path),
        }


def extract_text(obj):
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, (int, float, bool)):
        return str(obj)
    if isinstance(obj, list):
        parts = []
        for item in obj:
            text = extract_text(item)
            if text:
                parts.append(text)
        return "\n".join(parts).strip()
    if isinstance(obj, dict):
        for key in ("final_answer", "answer", "response", "reply", "message", "text", "content", "output", "result", "summary"):
            if key in obj:
                text = extract_text(obj.get(key))
                if text:
                    return text
        for key in ("data", "payload", "details", "body"):
            if key in obj:
                text = extract_text(obj.get(key))
                if text:
                    return text
        return ""
    return str(obj)


def discover_backend_routes(base_url: str) -> list[str]:
    openapi_url = base_url.rstrip("/") + "/openapi.json"
    try:
        req = Request(openapi_url, headers={"User-Agent": "JarvisOperatorPanel/4.0"})
        with urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            paths = data.get("paths", {})
            if isinstance(paths, dict):
                return list(paths.keys())
    except Exception:
        pass
    return []


def _http_get_json(url: str, timeout: int = 20) -> dict:
    req = Request(url, headers={"User-Agent": "JarvisOperatorPanel/4.0"})
    with urlopen(req, timeout=timeout) as resp:
        text = resp.read().decode("utf-8", errors="replace")
        return json.loads(text)


def _http_json(url: str, payload: dict, timeout: int = 120) -> dict:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(
        url,
        data=raw,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "JarvisOperatorPanel/4.0",
        },
        method="POST",
    )
    with urlopen(req, timeout=timeout) as resp:
        text = resp.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(text)
        except Exception:
            data = {"raw_text": text}
        normalized = extract_text(data) or text
        return {
            "ok": True,
            "raw": data,
            "normalized_text": normalized,
        }


def get_agents_registry(base_url: str) -> dict:
    return _http_get_json(base_url.rstrip("/") + "/api/agents/registry", timeout=20)


def get_agents_adapters(base_url: str) -> dict:
    return _http_get_json(base_url.rstrip("/") + "/api/agents/adapters", timeout=20)


def summarize_services_and_agents(base_url: str) -> str:
    parts = []
    try:
        adapters = get_agents_adapters(base_url)
        items = _extract_items(adapters)
        if items:
            parts.append("Сейчас у меня подтверждённо доступны такие адаптеры:")
            for item in items[:8]:
                name = item.get("name") or item.get("adapter_name") or item.get("display_name")
                caps = ", ".join(item.get("capabilities", []))
                parts.append(f"- {name} — type={item.get('type')}, capabilities={caps}")
    except Exception as e:
        parts.append(f"Не удалось прочитать adapters: {e}")
    try:
        registry = get_agents_registry(base_url)
        items = _extract_items(registry)
        if items:
            parts.append("")
            parts.append("И вот какие AI-агенты сейчас есть в registry:")
            for item in items[:10]:
                caps = ", ".join(item.get("capabilities", []))
                parts.append(
                    f"- {item.get('display_name') or item.get('name')} ({item.get('agent_id')}) — "
                    f"adapter={item.get('adapter_name')}, capabilities={caps}"
                )
    except Exception as e:
        parts.append(f"Не удалось прочитать registry: {e}")
    return "\n".join(parts).strip() if parts else "Пока не удалось получить список сервисов и агентов."


def choose_chat_agent(base_url: str) -> dict | None:
    try:
        registry = get_agents_registry(base_url)
        items = _extract_items(registry)
    except Exception:
        return None
    scored = []
    for item in items:
        if item.get("enabled") is False:
            continue
        caps = [str(x).lower() for x in item.get("capabilities", [])]
        adapter = str(item.get("adapter_name") or "").lower()
        name = str(item.get("display_name") or item.get("name") or "").lower()
        score = 0
        if "chat" in caps:
            score += 20
        if "reasoning" in caps:
            score += 10
        if "ollama" in adapter or "ollama" in name:
            score += 30
        if "local" in adapter or "echo" in adapter or "local" in name or "echo" in name:
            score += 5
        if "openai" in adapter or "claude" in adapter:
            score += 1
        scored.append((score, item))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1] if scored else None


def try_backend_memory_remember(base_url: str, text_value: str) -> dict:
    discovered = discover_backend_routes(base_url)
    if discovered and "/api/memory/remember" not in discovered:
        return {"ok": False, "reason": "route_missing", "discovered_routes": discovered}
    url = base_url.rstrip("/") + "/api/memory/remember"
    payloads = [
        {"text": text_value},
        {"content": text_value},
        {"memory": text_value},
        {"message": text_value},
        {"entry": text_value},
        {"note": text_value},
        {"text": text_value, "source": "panel_chat"},
    ]
    attempts = []
    for payload in payloads:
        try:
            result = _http_json(url, payload, timeout=30)
            return {"ok": True, "payload_used": payload, "raw": result.get("raw")}
        except Exception as e:
            attempts.append({"payload": payload, "error": str(e)})
    return {"ok": False, "reason": "all_payloads_failed", "tried": attempts[-10:]}


def try_agents_invoke(base_url: str, message: str, mode: str) -> dict:
    discovered = discover_backend_routes(base_url)
    if discovered and "/api/agents/invoke" not in discovered:
        return {"ok": False, "reason": "route_missing", "discovered_routes": discovered}
    url = base_url.rstrip("/") + "/api/agents/invoke"
    chosen = choose_chat_agent(base_url)
    payloads = []
    base_variants = []
    for key in ("message", "text", "input", "instruction", "task", "prompt", "query", "content"):
        base = {key: message}
        if mode and mode != "default":
            base["mode"] = mode
        base_variants.append(base)
    payloads.extend(base_variants)
    if chosen:
        agent_id = chosen.get("agent_id")
        agent_name = chosen.get("display_name") or chosen.get("name")
        adapter_name = chosen.get("adapter_name")
        for base in base_variants:
            if agent_id:
                p = dict(base)
                p["agent_id"] = agent_id
                payloads.append(p)
                payloads.append({"agent_id": agent_id, "payload": base})
            if agent_name:
                p3 = dict(base)
                p3["agent_name"] = agent_name
                payloads.append(p3)
            if adapter_name:
                payloads.append({"adapter_name": adapter_name, "payload": base})
    attempts = []
    for payload in payloads:
        try:
            result = _http_json(url, payload, timeout=150)
            raw = result.get("raw", {})
            status = raw.get("status") if isinstance(raw, dict) else None
            approval_status = None
            if isinstance(raw, dict) and isinstance(raw.get("approval"), dict):
                approval_status = raw["approval"].get("status")
            if status == "approval_required" or approval_status == "pending":
                attempts.append({"payload": payload, "error": "approval_required"})
                continue
            normalized = result.get("normalized_text", "")
            if normalized:
                return {
                    "ok": True,
                    "payload_used": payload,
                    "raw": raw,
                    "normalized_text": normalized,
                    "chosen_agent": chosen,
                }
        except Exception as e:
            attempts.append({"payload": payload, "error": str(e)})
    return {"ok": False, "reason": "all_payloads_failed", "tried": attempts[-20:], "chosen_agent": chosen}


def looks_like_storage_question(text: str) -> bool:
    patterns = [
        r"(куда|где).*(сохран|хран).*(данн|памят)",
        r"куда ты это",
        r"где это хранится",
        r"куда ты сохраняешь данные",
        r"где ты сохраняешь данные",
        r"где хранятся данные",
    ]
    return any(re.search(p, text, flags=re.I) for p in patterns)


def looks_like_name_question(text: str) -> bool:
    patterns = [
        r"как тебя зовут",
        r"кто ты",
        r"твое имя",
        r"твоё имя",
        r"как твое имя",
        r"как твоё имя",
    ]
    return any(re.search(p, text, flags=re.I) for p in patterns)


def looks_like_services_question(text: str) -> bool:
    patterns = [
        r"какие.*сервис",
        r"какие.*агент",
        r"ии.*агент",
        r"ai.*agent",
        r"какие.*адаптер",
        r"какие.*интеграц",
        r"какие.*доступн.*сервис",
        r"какие.*доступн.*агент",
    ]
    return any(re.search(p, text, flags=re.I) for p in patterns)


def local_first_intent(text: str) -> bool:
    lower = text.lower().strip()
    if re.fullmatch(r"(привет|здравствуй|hello|hi)\W*", lower):
        return True
    checks = [
        looks_like_storage_question(lower),
        looks_like_name_question(lower),
        looks_like_services_question(lower),
        any(x in lower for x in [
            "что ты умеешь", "как ты себя чувствуешь", "как дела", "статус", "health", "status",
            "гугл диск", "google drive", "google console", "гугл консоль",
            "api ключ", "api keys", "покажи runs", "real run", "phase2", "normalize",
            "перезапусти backend", "как меня зовут", "что ты помнишь", "запомни", "remember",
            "создай задачу", "запусти задачу", "run task", "run mission"
        ]),
    ]
    return any(checks)


def summarize_health(base_url: str) -> str:
    items = [safe_probe(base_url, ep, name) for ep, name in PROBE_ENDPOINTS]
    summary = build_probe_summary(items)
    lines = []
    if summary["critical_ok"]:
        lines.append("Я в хорошем состоянии. Критических проблем сейчас не вижу.")
    else:
        lines.append("Есть несколько узких мест, на которые стоит посмотреть внимательнее.")
    lines.append(
        f"По текущему состоянию: стабильных проверок — {summary['ok_count']}, "
        f"необязательных пропусков — {summary['optional_missing_count']}, критических ошибок — {summary['bad_count']}."
    )
    bad_items = [x for x in items if x["level"] == "bad"]
    if bad_items:
        lines.append("Вот что требует внимания:")
        for item in bad_items[:5]:
            lines.append(f"- {item['path']}: {item['status_text']} ({item['error']})")
    else:
        lines.append("Базовый операторский контур панели и backend сейчас выглядят устойчиво.")
    return "\n".join(lines)


def summarize_memory(project_root: Path) -> str:
    data = load_panel_memory(project_root)
    items = data.get("items", [])
    if not items:
        return "Пока в локальной памяти панели у меня ничего нет."
    jarvis_name = ""
    user_name = ""
    for item in reversed(items):
        text = str(item.get("text", ""))
        if not jarvis_name:
            m = re.search(r"тво[её]\s+имя\s+([A-Za-zА-Яа-яЁёІіЇїЄєҐґ0-9_-]+)", text, flags=re.I)
            if m:
                jarvis_name = m.group(1).strip()
        if not user_name:
            m = re.search(r"меня\s+зовут\s+([A-ЯA-ZЁІЇЄҐ][^.,;\n]+)", text, flags=re.I)
            if m:
                user_name = m.group(1).strip()
        if jarvis_name and user_name:
            break
    lines = []
    if jarvis_name:
        lines.append(f"Я помню, что меня зовут {jarvis_name}.")
    if user_name:
        lines.append(f"И я помню, что тебя зовут {user_name}.")
    if not lines:
        lines.append("Вот что сейчас сохранено в памяти панели:")
        for item in items[-5:]:
            lines.append(f"- {item.get('timestamp','')} | {item.get('text','')}")
    return "\n".join(lines)


def backend_chat_request(base_url: str, message: str, mode: str) -> dict:
    result = try_agents_invoke(base_url, message, mode)
    if result.get("ok"):
        return {
            "ok": True,
            "route": "/api/agents/invoke",
            "transport": "json",
            "payload_used": result.get("payload_used"),
            "raw": result.get("raw"),
            "normalized_text": result.get("normalized_text", ""),
            "chosen_agent": result.get("chosen_agent"),
            "discovered_routes": discover_backend_routes(base_url),
        }
    return {
        "ok": False,
        "error": "Chat bridge could not find a working backend contract.",
        "discovered_routes": discover_backend_routes(base_url),
        "tried": result.get("tried", []),
        "chosen_agent": result.get("chosen_agent"),
    }


def try_create_and_run_goal(base_url: str, objective: str, constraints: list[str] | None = None) -> dict:
    routes = discover_backend_routes(base_url)
    if routes and "/api/goals" not in routes:
        return {"ok": False, "reason": "route_missing", "routes": routes}
    constraints = constraints or []
    payloads = [
        {"objective": objective, "constraints": constraints},
        {"goal": objective, "constraints": constraints},
        {"text": objective, "constraints": constraints},
        {"objective": objective, "summary": objective, "constraints": constraints},
    ]
    goal_response = None
    last_errors = []
    for payload in payloads:
        try:
            result = _http_json(base_url.rstrip("/") + "/api/goals", payload, timeout=60)
            goal_response = {"payload": payload, "result": result}
            break
        except Exception as e:
            last_errors.append({"payload": payload, "error": str(e)})
    if not goal_response:
        return {"ok": False, "reason": "goal_create_failed", "errors": last_errors[-10:]}
    raw = goal_response["result"].get("raw", {})
    mission_id = ""
    goal_id = ""
    if isinstance(raw, dict):
        mission_id = str(raw.get("mission_id") or raw.get("data", {}).get("mission_id") or "").strip() if isinstance(raw.get("data"), dict) else str(raw.get("mission_id") or "").strip()
        goal_id = str(raw.get("goal_id") or "").strip()
        if not mission_id:
            for key in ("mission", "data", "payload", "result"):
                val = raw.get(key)
                if isinstance(val, dict) and val.get("mission_id"):
                    mission_id = str(val.get("mission_id")).strip()
                    break
    run_result = None
    if mission_id:
        run_url = base_url.rstrip("/") + f"/api/missions/{mission_id}/run"
        try:
            run_result = _http_json(run_url, {"mode": "operator"}, timeout=120)
        except Exception as e:
            run_result = {"ok": False, "error": str(e)}
    normalized = extract_text(raw) or json.dumps(raw, ensure_ascii=False, indent=2)
    return {
        "ok": True,
        "goal_id": goal_id,
        "mission_id": mission_id,
        "create_payload": goal_response["payload"],
        "create_raw": raw,
        "create_text": normalized,
        "run_result": run_result,
    }


def execute_operator_action(project_root: Path, action: str) -> dict:
    action = (action or "").strip()
    if action == "restart_backend":
        return run_powershell_file(project_root, "restart_backend.ps1")
    if action == "phase2_latest":
        latest = find_latest_run_dir(project_root)
        if not latest:
            return {"ok": False, "error": "no real runs found"}
        result = run_powershell_file(project_root, "jarvis_sop_phase2_finalize.ps1", ["-ProjectRoot", str(project_root)])
        result["latest_run"] = str(latest)
        return result
    if action == "normalize_latest":
        latest = find_latest_run_dir(project_root)
        if not latest:
            return {"ok": False, "error": "no real runs found"}
        normalize_py = project_root / "scripts" / "jarvis_artifact_normalize_utf8.py"
        pyexe = project_root / ".venv" / "Scripts" / "python.exe"
        py = str(pyexe if pyexe.exists() else "python")
        try:
            proc = subprocess.run(
                [
                    py, str(normalize_py),
                    "--run-dir", str(latest),
                    "--recursive",
                    "--pattern", "*.md",
                    "--pattern", "*.txt",
                    "--pattern", "*.json",
                    "--pattern", "*.log",
                ],
                cwd=str(project_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
            )
            return {
                "ok": proc.returncode == 0,
                "returncode": proc.returncode,
                "stdout": proc.stdout[-16000:],
                "stderr": proc.stderr[-16000:],
                "latest_run": str(latest),
            }
        except subprocess.TimeoutExpired as e:
            return {
                "ok": False,
                "error": "normalize timeout",
                "latest_run": str(latest),
                "stdout": (e.stdout or "")[-16000:] if isinstance(e.stdout, str) else "",
                "stderr": (e.stderr or "")[-16000:] if isinstance(e.stderr, str) else "",
            }
    return {"ok": False, "error": f"unknown_action: {action}"}


def operator_chat_fallback(project_root: Path, base_url: str, message: str, mode: str, backend_result: dict | None = None) -> dict:
    lower = message.lower().strip()
    data = load_panel_memory(project_root)
    items = data.get("items", [])
    jarvis_name = "Jarvis"
    user_name = ""
    for item in reversed(items):
        text = str(item.get("text", ""))
        if jarvis_name == "Jarvis":
            m = re.search(r"тво[её]\s+имя\s+([A-Za-zА-Яа-яЁёІіЇїЄєҐґ0-9_-]+)", text, flags=re.I)
            if m:
                jarvis_name = m.group(1).strip()
        if not user_name:
            m = re.search(r"меня\s+зовут\s+([A-ЯA-ZЁІЇЄҐ][^.,;\n]+)", text, flags=re.I)
            if m:
                user_name = m.group(1).strip()
        if jarvis_name != "Jarvis" and user_name:
            break

    if re.fullmatch(r"(привет|здравствуй|hello|hi)\W*", lower):
        answer = f"{jarvis_name} на связи. Рад тебя видеть."
        if user_name:
            answer += f"\nОбращаюсь к тебе как к {user_name}."
        answer += "\nМожешь спросить меня про состояние системы, память панели, сервисы, агентов или поставить задачу."
        return {"ok": True, "source": "fallback_greeting", "answer": answer}

    if any(x in lower for x in ["как ты себя чувствуешь", "как дела", "здоров", "health", "status", "статус"]):
        answer = f"{jarvis_name} на связи.\n\n" + summarize_health(base_url)
        return {"ok": True, "source": "fallback_health", "answer": answer}

    if looks_like_name_question(lower):
        answer = f"Меня зовут {jarvis_name}."
        if user_name:
            answer += f"\nА тебя я помню как {user_name}."
        else:
            answer += "\nЕсли хочешь, я могу запомнить, как к тебе обращаться."
        return {"ok": True, "source": "fallback_name", "answer": answer}

    if any(x in lower for x in ["что ты умеешь", "что умеешь", "what can you do", "help", "помощь", "что ты сейчас умеешь"]):
        answer = (
            f"{jarvis_name} сейчас умеет следующее:\n"
            "- следить за состоянием backend и панели;\n"
            "- показывать health, probes, hardening и последние real runs;\n"
            "- выполнять operator actions: restart backend, Phase2 latest, normalize latest;\n"
            "- принимать задачи через Task runner и пробовать создать/запустить mission через backend;\n"
            "- запоминать локальные заметки через панель;\n"
            "- показывать доступные сервисы, адаптеры и AI-агентов;\n"
            "- отвечать через /api/agents/invoke, а если это не удаётся — переходить на устойчивый локальный fallback."
        )
        return {"ok": True, "source": "fallback_help", "answer": answer}

    if looks_like_services_question(lower):
        return {"ok": True, "source": "fallback_services", "answer": summarize_services_and_agents(base_url)}

    if looks_like_storage_question(lower):
        memory_path = project_root / "jarvis_stage3_artifacts" / "panel_chat_memory.json"
        answer = (
            "Я храню записи этого panel-чата в локальной памяти панели.\n\n"
            f"Путь: {memory_path}\n\n"
            "То есть сейчас это локальный файл внутри jarvis_stage3_artifacts. Backend memory route есть, "
            "но как основной контракт для этого panel-чата он пока ещё не закреплён."
        )
        return {"ok": True, "source": "fallback_memory_location", "answer": answer}

    if any(x in lower for x in ["покажи ран", "покажи runs", "latest runs", "артефакт", "артефакты", "последние ран", "real run"]):
        return {"ok": True, "source": "fallback_runs", "answer": summarize_runs(project_root)}

    if "перезапусти backend" in lower or "restart backend" in lower:
        result = execute_operator_action(project_root, "restart_backend")
        text = "Перезапуск backend выполнен." if result.get("ok") else "Перезапуск backend завершился с ошибкой."
        answer = text + "\n\nstdout:\n" + (result.get("stdout", "")[-1200:] or "") + "\n\nstderr:\n" + (result.get("stderr", "")[-1200:] or "")
        return {"ok": True, "source": "fallback_restart_backend", "answer": answer}

    if "phase2" in lower:
        result = execute_operator_action(project_root, "phase2_latest")
        text = "Phase2 latest выполнен." if result.get("ok") else "Phase2 latest завершился с ошибкой."
        answer = text + "\n\n" + json.dumps(result, ensure_ascii=False, indent=2)
        return {"ok": True, "source": "fallback_phase2", "answer": answer}

    if "normalize" in lower:
        result = execute_operator_action(project_root, "normalize_latest")
        text = "Нормализация latest run выполнена." if result.get("ok") else "Нормализация latest run завершилась с ошибкой."
        answer = text + "\n\n" + json.dumps(result, ensure_ascii=False, indent=2)
        return {"ok": True, "source": "fallback_normalize", "answer": answer}

    if any(x in lower for x in ["запомни", "remember"]):
        remembered = extract_remember_text(message)
        add_panel_memory(project_root, remembered)
        backend_store = try_backend_memory_remember(base_url, remembered)
        answer = f"Хорошо. Я запомнил это локально: {remembered}."
        if backend_store.get("ok"):
            answer += "\nЗапись также успешно ушла в backend memory."
        else:
            answer += "\nПока это гарантированно сохранено в локальной памяти панели; backend memory route ещё нужно довести отдельно."
        return {"ok": True, "source": "fallback_memory_write", "answer": answer}

    if any(x in lower for x in ["как меня зовут", "кто я", "что ты помнишь", "что ты запомнил", "remembered", "memory"]):
        if "как меня зовут" in lower and user_name:
            answer = f"Тебя зовут {user_name}."
            if jarvis_name:
                answer += f"\nА меня ты попросил называть {jarvis_name}."
            return {"ok": True, "source": "fallback_memory_read", "answer": answer}
        return {"ok": True, "source": "fallback_memory_read", "answer": summarize_memory(project_root)}

    if any(x in lower for x in ["создай задачу", "запусти задачу", "run task", "run mission", "сделай задачу"]):
        return {"ok": True, "source": "fallback_task_hint", "answer": "Чтобы надёжно поставить задачу, используй Task runner в панели v4: укажи objective и mode=task/operator. Панель попробует создать mission через backend, а если маршрут недоступен — отправит задачу через /api/agents/invoke."}

    if re.search(r"^(кто|что|сколько|почему|зачем|как)\b", lower):
        answer = (
            f"{jarvis_name} на связи.\n\n"
            "Это уже похоже на общий разговорный или энциклопедический вопрос. "
            "Сейчас панель лучше всего работает как operator/task интерфейс для Jarvis-системы. "
            "Для широких общих ответов она использует backend chat-layer, если он доступен."
        )
        return {"ok": True, "source": "fallback_general_limit", "answer": answer}

    answer = (
        f"{jarvis_name} на связи.\n\n"
        "Я понял запрос. В панели v4 лучше всего работают operator actions, Task runner, состояние системы, память панели, сервисы, агенты и последние runs. "
        "Если задача не относится к локальному операторскому набору, я пробую прогнать её через backend mission/chat layer."
    )
    return {"ok": True, "source": "fallback_info", "answer": answer}


def handle_chat(project_root: Path, base_url: str, message: str, mode: str) -> dict:
    message = (message or "").strip()
    mode = (mode or "default").strip() or "default"
    if not message:
        return {"ok": False, "error": "empty_message"}
    add_ui_message(project_root, "user", message)
    try:
        if local_first_intent(message):
            fallback = operator_chat_fallback(project_root, base_url, message, mode, backend_result=None)
            answer = fallback.get("answer", "")
            add_ui_message(project_root, "assistant", answer)
            return {
                "ok": True,
                "mode": mode,
                "answer": answer,
                "debug": {
                    "source": fallback.get("source"),
                    "route_strategy": "local_first",
                },
            }
        result = backend_chat_request(base_url, message, mode)
        if result.get("ok"):
            answer = result.get("normalized_text", "")
            add_ui_message(project_root, "assistant", answer)
            return {
                "ok": True,
                "mode": mode,
                "answer": answer,
                "debug": {
                    "source": "backend_chat_request",
                    "payload_used": result.get("payload_used"),
                    "route": result.get("route"),
                    "transport": result.get("transport"),
                    "chosen_agent": result.get("chosen_agent"),
                },
            }
        fallback = operator_chat_fallback(project_root, base_url, message, mode, backend_result=result)
        answer = fallback.get("answer", "")
        add_ui_message(project_root, "assistant", answer)
        return {
            "ok": True,
            "mode": mode,
            "answer": answer,
            "debug": {
                "source": fallback.get("source"),
                "backend_bridge_error": result.get("error"),
                "discovered_routes": result.get("discovered_routes", []),
                "chosen_agent": result.get("chosen_agent"),
                "route_strategy": "backend_then_fallback",
            },
        }
    except Exception:
        text = traceback.format_exc(limit=4)
        add_ui_message(project_root, "assistant", "Ошибка обработки чата.\n\n" + text)
        set_last_error(project_root, text)
        return {"ok": False, "error": text}


def handle_task(project_root: Path, base_url: str, objective: str, mode: str, constraints_text: str = "") -> dict:
    objective = (objective or "").strip()
    mode = (mode or "task").strip() or "task"
    constraints = [x.strip() for x in re.split(r"[\r\n]+", constraints_text or "") if x.strip()]
    if not objective:
        return {"ok": False, "error": "empty_objective"}
    add_ui_message(project_root, "user", f"[TASK/{mode}] {objective}")
    try:
        if mode in ("task", "operator", "mission"):
            mission_result = try_create_and_run_goal(base_url, objective, constraints=constraints)
            if mission_result.get("ok"):
                lines = ["Задача принята."]
                if mission_result.get("goal_id"):
                    lines.append(f"goal_id={mission_result['goal_id']}")
                if mission_result.get("mission_id"):
                    lines.append(f"mission_id={mission_result['mission_id']}")
                lines.append("")
                lines.append("Результат создания:")
                lines.append(mission_result.get("create_text", ""))
                if mission_result.get("run_result") is not None:
                    lines.append("")
                    lines.append("Результат запуска:")
                    lines.append(json.dumps(mission_result.get("run_result"), ensure_ascii=False, indent=2))
                answer = "\n".join(lines).strip()
                add_ui_message(project_root, "assistant", answer)
                set_last_task_result(project_root, answer)
                return {
                    "ok": True,
                    "answer": answer,
                    "debug": {
                        "strategy": "goal_then_run",
                        "mission_id": mission_result.get("mission_id"),
                        "goal_id": mission_result.get("goal_id"),
                    },
                }

        chat_like_message = "Выполни задачу:\n" + objective
        if constraints:
            chat_like_message += "\n\nОграничения:\n- " + "\n- ".join(constraints)
        chat_result = backend_chat_request(base_url, chat_like_message, mode)
        if chat_result.get("ok"):
            answer = chat_result.get("normalized_text", "")
            add_ui_message(project_root, "assistant", answer)
            set_last_task_result(project_root, answer)
            return {
                "ok": True,
                "answer": answer,
                "debug": {
                    "strategy": "agents_invoke_task_fallback",
                    "chosen_agent": chat_result.get("chosen_agent"),
                    "payload_used": chat_result.get("payload_used"),
                },
            }

        fallback = operator_chat_fallback(project_root, base_url, objective, mode, backend_result=chat_result)
        answer = "Task runner не нашёл стабильный mission route, поэтому дал локальный operator fallback.\n\n" + fallback.get("answer", "")
        add_ui_message(project_root, "assistant", answer)
        set_last_task_result(project_root, answer)
        return {
            "ok": True,
            "answer": answer,
            "debug": {
                "strategy": "operator_fallback",
                "source": fallback.get("source"),
            },
        }
    except Exception:
        text = traceback.format_exc(limit=6)
        add_ui_message(project_root, "assistant", "Ошибка Task runner.\n\n" + text)
        set_last_error(project_root, text)
        return {"ok": False, "error": text}


def build_page(app, notice: str = "") -> str:
    probe_items = [safe_probe(app.backend_base_url, ep, name) for ep, name in PROBE_ENDPOINTS]
    probe_summary = build_probe_summary(probe_items)
    hardening = hardening_report(app.project_root).get("checks", {})
    runs = list_real_runs(app.project_root, limit=8)
    state = load_ui_state(app.project_root)
    messages = state.get("messages", [])

    probe_rows = []
    for item in probe_items:
        probe_rows.append(
            "<tr>"
            f"<td class='mono'>{esc(item['name'])}</td>"
            f"<td>{esc(item['path'])}</td>"
            f"<td class='{esc(item['level'])}'>{esc(item['status_text'])}</td>"
            f"<td>{esc(item['elapsed_ms'])} ms</td>"
            f"<td>{esc(item['summary'] or item['error'])}</td>"
            "</tr>"
        )

    hardening_rows = []
    for key, value in hardening.items():
        cls = "ok" if value is True else "bad"
        hardening_rows.append(
            "<tr>"
            f"<td class='mono'>{esc(key)}</td>"
            f"<td class='{cls}'>{esc(value)}</td>"
            "</tr>"
        )

    run_rows = []
    if runs:
        for item in runs:
            files = ", ".join(f"{x['name']} ({x['size']})" for x in item.get("files", [])) or "без файлов"
            run_rows.append(
                "<tr>"
                f"<td class='mono'>{esc(item['name'])}</td>"
                f"<td>{esc(item['updated_at'])}</td>"
                f"<td>{esc(files)}</td>"
                "</tr>"
            )
    else:
        run_rows.append("<tr><td colspan='3' class='warn'>No real run directories found.</td></tr>")

    msg_rows = []
    msg_rows.append(
        "<div class='msg system'>"
        "<div class='role'>system</div>"
        "Jarvis v4 online. Эта версия серверно-рендерится и не зависит от фронтового JS как от основы."
        "</div>"
    )
    for msg in messages[-40:]:
        role = str(msg.get("role", "assistant"))
        text = str(msg.get("text", ""))
        ts = str(msg.get("timestamp", ""))
        css = "user" if role == "user" else ("assistant" if role == "assistant" else "system")
        msg_rows.append(
            f"<div class='msg {esc(css)}'>"
            f"<div class='role'>{esc(role)} · {esc(ts)}</div>"
            f"{esc(text).replace(chr(10), '<br>')}"
            "</div>"
        )

    notice_html = f"<div class='notice'>{esc(notice)}</div>" if notice else ""
    error_html = f"<div class='errorbox'><strong>Last error:</strong><br>{esc(state.get('last_error','')).replace(chr(10), '<br>')}</div>" if state.get("last_error") else ""

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Jarvis Operator Panel v4</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root{{--bg:#0f172a;--panel:#111827;--panel2:#020617;--border:#334155;--text:#e2e8f0;--muted:#94a3b8;--blue:#2563eb;--blue2:#1d4ed8;--green:#22c55e;--yellow:#f59e0b;--red:#ef4444}}
*{{box-sizing:border-box}}
body{{font-family:Arial,Helvetica,sans-serif;margin:0;background:var(--bg);color:var(--text)}}
header{{padding:16px 20px;background:#111827;position:sticky;top:0;border-bottom:1px solid var(--border);z-index:10}}
h1{{margin:0;font-size:24px}}
small{{color:var(--muted)}}
.wrap{{padding:16px 20px}}
.layout{{display:grid;grid-template-columns:minmax(340px,430px) minmax(520px,1fr);gap:16px}}
.col{{display:flex;flex-direction:column;gap:16px}}
.card{{background:var(--panel);border:1px solid var(--border);border-radius:18px;padding:16px;box-shadow:0 6px 24px rgba(0,0,0,.22)}}
.card h2{{margin:0 0 12px 0;font-size:18px}}
button{{background:var(--blue);color:white;border:none;border-radius:10px;padding:10px 14px;cursor:pointer}}
button:hover{{background:var(--blue2)}}
button.secondary{{background:#334155}}
button.secondary:hover{{background:#475569}}
pre{{white-space:pre-wrap;word-break:break-word;background:var(--panel2);border-radius:12px;padding:12px;border:1px solid var(--border);max-height:420px;overflow:auto}}
.ok{{color:var(--green)}}
.warn{{color:var(--yellow)}}
.bad{{color:var(--red)}}
.optional_missing{{color:var(--yellow)}}
.mono{{font-family:Consolas,Menlo,monospace}}
.table{{width:100%;border-collapse:collapse;font-size:13px}}
.table th,.table td{{border-bottom:1px solid var(--border);padding:8px;text-align:left;vertical-align:top}}
.actions{{display:flex;gap:10px;align-items:center;flex-wrap:wrap}}
textarea,input[type=text]{{width:100%;background:var(--panel2);color:var(--text);border:1px solid var(--border);border-radius:12px;padding:12px;font:inherit}}
textarea{{min-height:120px;resize:vertical}}
select{{background:var(--panel2);color:var(--text);border:1px solid var(--border);border-radius:10px;padding:10px}}
.form-row{{display:flex;gap:10px;flex-wrap:wrap}}
.form-row > *{{flex:1}}
.chat-messages{{display:flex;flex-direction:column;gap:12px;max-height:72vh;overflow:auto;padding-right:4px}}
.msg{{max-width:88%;padding:12px 14px;border-radius:16px;border:1px solid var(--border);white-space:pre-wrap;word-break:break-word;line-height:1.45}}
.msg.user{{align-self:flex-end;background:#172554}}
.msg.assistant{{align-self:flex-start;background:#0b1220}}
.msg.system{{align-self:center;background:#1f2937;color:#cbd5e1}}
.msg .role{{font-size:12px;color:var(--muted);margin-bottom:6px}}
.notice{{margin:0 0 12px 0;padding:10px 12px;border-radius:12px;background:#0b3b2e;border:1px solid #14532d;color:#d1fae5}}
.errorbox{{margin:0 0 12px 0;padding:10px 12px;border-radius:12px;background:#3a0d0d;border:1px solid #7f1d1d;color:#fecaca}}
.section-note{{color:var(--muted);font-size:13px}}
a.btnlink{{display:inline-block;text-decoration:none}}
@media (max-width:1200px){{.layout{{grid-template-columns:1fr}}}}
</style>
</head>
<body>
<header>
  <h1>Jarvis Operator Panel v4</h1>
  <small>project_root={esc(app.project_root)} | backend={esc(app.backend_base_url)} | panel=http://{esc(app.host)}:{esc(app.port)}/</small>
</header>
<div class="wrap">
  {notice_html}
  {error_html}
  <div class="layout">
    <div class="col">
      <div class="card">
        <h2>Operator actions</h2>
        <form method="post" action="/ui/action">
          <div class="actions">
            <button type="submit" name="action" value="refresh">Refresh page</button>
            <button type="submit" class="secondary" name="action" value="restart_backend">Restart backend</button>
            <button type="submit" class="secondary" name="action" value="phase2_latest">Phase2 latest</button>
            <button type="submit" class="secondary" name="action" value="normalize_latest">Normalize latest</button>
            <button type="submit" class="secondary" name="action" value="clear_chat">Clear chat</button>
          </div>
        </form>
        <div class="section-note">Серверно-рендерная версия: действия работают без обязательного JavaScript.</div>
      </div>

      <div class="card">
        <h2>Probe summary</h2>
        <table class="table">
          <tr><td>critical_ok</td><td>{esc(probe_summary['critical_ok'])}</td></tr>
          <tr><td>ok_count</td><td>{esc(probe_summary['ok_count'])}</td></tr>
          <tr><td>optional_missing_count</td><td>{esc(probe_summary['optional_missing_count'])}</td></tr>
          <tr><td>bad_count</td><td>{esc(probe_summary['bad_count'])}</td></tr>
        </table>
      </div>

      <div class="card">
        <h2>Probe details</h2>
        <table class="table">
          <thead><tr><th>Name</th><th>Path</th><th>Status</th><th>Latency</th><th>Summary</th></tr></thead>
          <tbody>{''.join(probe_rows)}</tbody>
        </table>
      </div>

      <div class="card">
        <h2>Hardening status</h2>
        <table class="table">
          <thead><tr><th>Check</th><th>Value</th></tr></thead>
          <tbody>{''.join(hardening_rows)}</tbody>
        </table>
      </div>

      <div class="card">
        <h2>Recent real runs</h2>
        <table class="table">
          <thead><tr><th>Run dir</th><th>Updated</th><th>Files</th></tr></thead>
          <tbody>{''.join(run_rows)}</tbody>
        </table>
      </div>

      <div class="card">
        <h2>Last action result</h2>
        <pre>{esc(state.get('last_action_result', '') or 'No actions yet.')}</pre>
      </div>

      <div class="card">
        <h2>Last task result</h2>
        <pre>{esc(state.get('last_task_result', '') or 'No tasks yet.')}</pre>
      </div>
    </div>

    <div class="col">
      <div class="card">
        <h2>Chat with Jarvis</h2>
        <div class="section-note">Обычный чат: local-first fallback для operator/introspection и backend chat bridge для остальных запросов.</div>
        <div class="chat-messages">{''.join(msg_rows)}</div>
      </div>

      <div class="card">
        <h2>Send chat message</h2>
        <form method="post" action="/ui/chat">
          <div class="form-row">
            <div>
              <label for="chat_mode">Mode</label><br>
              <select id="chat_mode" name="mode">
                <option value="default">default</option>
                <option value="operator">operator</option>
                <option value="task">task</option>
              </select>
            </div>
          </div>
          <div style="margin-top:12px">
            <textarea name="message" placeholder="Напиши обычным текстом, что нужно сделать..."></textarea>
          </div>
          <div class="actions" style="margin-top:12px">
            <button type="submit">Send</button>
          </div>
        </form>
      </div>

      <div class="card">
        <h2>Task runner</h2>
        <div class="section-note">Здесь Jarvis уже пытается не просто отвечать, а ставить и запускать задачи через mission/backend слой.</div>
        <form method="post" action="/ui/task">
          <div class="form-row">
            <div>
              <label for="task_mode">Mode</label><br>
              <select id="task_mode" name="mode">
                <option value="task">task</option>
                <option value="operator">operator</option>
                <option value="mission">mission</option>
              </select>
            </div>
          </div>
          <div style="margin-top:12px">
            <label for="objective">Objective</label><br>
            <textarea id="objective" name="objective" placeholder="Например: Собери статус по backend, проверь health и подготовь краткий отчёт."></textarea>
          </div>
          <div style="margin-top:12px">
            <label for="constraints">Constraints (по одной строке)</label><br>
            <textarea id="constraints" name="constraints" placeholder="Например:&#10;Не трогай production&#10;Используй только локальные endpoints"></textarea>
          </div>
          <div class="actions" style="margin-top:12px">
            <button type="submit">Run task</button>
          </div>
        </form>
      </div>

      <div class="card">
        <h2>JSON API quick links</h2>
        <div class="section-note">
          <a class="btnlink" href="/api/meta">/api/meta</a> ·
          <a class="btnlink" href="/api/probe">/api/probe</a> ·
          <a class="btnlink" href="/api/probe/summary">/api/probe/summary</a> ·
          <a class="btnlink" href="/api/hardening">/api/hardening</a> ·
          <a class="btnlink" href="/api/runs">/api/runs</a>
        </div>
      </div>
    </div>
  </div>
</div>
</body>
</html>"""


class App:
    def __init__(self, project_root: Path, backend_base_url: str, host: str, port: int):
        self.project_root = project_root
        self.backend_base_url = backend_base_url.rstrip("/")
        self.host = host
        self.port = port

    def handler(self):
        app = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                return

            def _send_json(self, payload: dict, status: int = 200):
                body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _send_html(self, html_text: str, status: int = 200):
                body = html_text.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _redirect(self, location: str):
                self.send_response(303)
                self.send_header("Location", location)
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
                self.end_headers()

            def _read_json_body(self) -> dict:
                length = int(self.headers.get("Content-Length", "0") or "0")
                raw = self.rfile.read(length) if length > 0 else b"{}"
                try:
                    return json.loads(raw.decode("utf-8", errors="replace"))
                except Exception:
                    return {}

            def _read_form_body(self) -> dict[str, str]:
                length = int(self.headers.get("Content-Length", "0") or "0")
                raw = self.rfile.read(length) if length > 0 else b""
                try:
                    data = parse_qs(raw.decode("utf-8", errors="replace"), keep_blank_values=True)
                    out = {}
                    for key, value in data.items():
                        out[key] = value[0] if value else ""
                    return out
                except Exception:
                    return {}

            def _notice_from_query(self, parsed) -> str:
                query = parse_qs(parsed.query)
                return (query.get("notice", [""])[0] or "").strip()

            def do_GET(self):
                parsed = urlparse(self.path)
                path = parsed.path

                if path == "/":
                    notice = self._notice_from_query(parsed)
                    self._send_html(build_page(app, notice=notice))
                    return

                if path == "/api/meta":
                    self._send_json({
                        "project_root": str(app.project_root),
                        "backend_base_url": app.backend_base_url,
                        "panel_url": f"http://{app.host}:{app.port}/",
                        "version": "v4_server_rendered",
                    })
                    return

                if path == "/api/probe":
                    items = [safe_probe(app.backend_base_url, ep, name) for ep, name in PROBE_ENDPOINTS]
                    self._send_json({"items": items})
                    return

                if path == "/api/probe/summary":
                    items = [safe_probe(app.backend_base_url, ep, name) for ep, name in PROBE_ENDPOINTS]
                    self._send_json(build_probe_summary(items))
                    return

                if path == "/api/runs":
                    query = parse_qs(parsed.query)
                    limit = 12
                    try:
                        if "limit" in query:
                            limit = max(1, min(50, int(query["limit"][0])))
                    except Exception:
                        limit = 12
                    self._send_json({"items": list_real_runs(app.project_root, limit=limit)})
                    return

                if path == "/api/hardening":
                    self._send_json(hardening_report(app.project_root))
                    return

                if path == "/api/state":
                    self._send_json(load_ui_state(app.project_root))
                    return

                self._send_json({"ok": False, "error": "not_found", "path": path}, status=404)

            def do_POST(self):
                parsed = urlparse(self.path)
                path = parsed.path

                if path == "/api/chat":
                    payload = self._read_json_body()
                    result = handle_chat(app.project_root, app.backend_base_url, str(payload.get("message", "")), str(payload.get("mode", "default")))
                    self._send_json(result, status=(200 if result.get("ok") else 500))
                    return

                if path == "/api/actions":
                    payload = self._read_json_body()
                    action = str(payload.get("action", "")).strip()
                    if action == "refresh":
                        result = {"ok": True, "action": "refresh", "message": "page refresh requested"}
                    elif action == "clear_chat":
                        clear_chat_state(app.project_root)
                        result = {"ok": True, "action": "clear_chat", "message": "chat cleared"}
                    else:
                        result = execute_operator_action(app.project_root, action)
                    text = json.dumps(result, ensure_ascii=False, indent=2)
                    set_last_action_result(app.project_root, text)
                    self._send_json({"ok": result.get("ok", False), "action": action, "result": result}, status=(200 if result.get("ok") else 500))
                    return

                if path == "/api/task":
                    payload = self._read_json_body()
                    result = handle_task(
                        app.project_root,
                        app.backend_base_url,
                        str(payload.get("objective", "")),
                        str(payload.get("mode", "task")),
                        str(payload.get("constraints", "")),
                    )
                    self._send_json(result, status=(200 if result.get("ok") else 500))
                    return

                if path == "/ui/chat":
                    payload = self._read_form_body()
                    result = handle_chat(app.project_root, app.backend_base_url, payload.get("message", ""), payload.get("mode", "default"))
                    notice = "Сообщение отправлено." if result.get("ok") else "Ошибка отправки сообщения."
                    self._redirect("/?notice=" + notice.replace(" ", "%20"))
                    return

                if path == "/ui/task":
                    payload = self._read_form_body()
                    result = handle_task(
                        app.project_root,
                        app.backend_base_url,
                        payload.get("objective", ""),
                        payload.get("mode", "task"),
                        payload.get("constraints", ""),
                    )
                    notice = "Задача отправлена." if result.get("ok") else "Ошибка запуска задачи."
                    self._redirect("/?notice=" + notice.replace(" ", "%20"))
                    return

                if path == "/ui/action":
                    payload = self._read_form_body()
                    action = payload.get("action", "").strip()
                    if action == "refresh":
                        set_last_action_result(app.project_root, "Page refreshed at " + time.strftime("%Y-%m-%d %H:%M:%S"))
                        self._redirect("/?notice=Page%20refreshed")
                        return
                    if action == "clear_chat":
                        clear_chat_state(app.project_root)
                        set_last_action_result(app.project_root, "Chat cleared at " + time.strftime("%Y-%m-%d %H:%M:%S"))
                        self._redirect("/?notice=Chat%20cleared")
                        return
                    result = execute_operator_action(app.project_root, action)
                    set_last_action_result(app.project_root, json.dumps(result, ensure_ascii=False, indent=2))
                    notice = f"Action {action} completed" if result.get("ok") else f"Action {action} failed"
                    self._redirect("/?notice=" + notice.replace(" ", "%20"))
                    return

                self._send_json({"ok": False, "error": "not_found", "path": path}, status=404)

        return Handler


def main():
    parser = argparse.ArgumentParser(description="Jarvis Operator Panel v4")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--backend-base-url", default="http://127.0.0.1:8015")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8028)
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    project_root.mkdir(parents=True, exist_ok=True)

    app = App(project_root=project_root, backend_base_url=args.backend_base_url, host=args.host, port=args.port)
    server = ThreadingHTTPServer((args.host, args.port), app.handler())
    print(json.dumps({
        "ok": True,
        "message": "Jarvis Operator Panel v4 started",
        "panel_url": f"http://{args.host}:{args.port}/",
        "backend_base_url": args.backend_base_url,
        "project_root": str(project_root),
        "mode": "server_rendered",
    }, ensure_ascii=False, indent=2))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
