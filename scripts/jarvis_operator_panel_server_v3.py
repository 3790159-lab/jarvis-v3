from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
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

HTML = """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>Jarvis Operator Panel v3</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root{
      --bg:#0f172a;--panel:#111827;--panel2:#020617;--border:#334155;
      --text:#e2e8f0;--muted:#94a3b8;--blue:#2563eb;--blue2:#1d4ed8;
      --green:#22c55e;--yellow:#f59e0b;--red:#ef4444
    }
    *{box-sizing:border-box}
    body{font-family:Arial,Helvetica,sans-serif;margin:0;background:var(--bg);color:var(--text)}
    header{padding:16px 20px;background:#111827;position:sticky;top:0;border-bottom:1px solid var(--border);z-index:10}
    h1{margin:0;font-size:24px}
    small{color:var(--muted)}
    .wrap{padding:16px 20px}
    .layout{display:grid;grid-template-columns:minmax(320px,420px) minmax(420px,1fr);gap:16px}
    .col{display:flex;flex-direction:column;gap:16px}
    .card{background:var(--panel);border:1px solid var(--border);border-radius:18px;padding:16px;box-shadow:0 6px 24px rgba(0,0,0,.22)}
    .card h2{margin:0 0 12px 0;font-size:18px}
    button{background:var(--blue);color:white;border:none;border-radius:10px;padding:10px 14px;cursor:pointer}
    button:hover{background:var(--blue2)}
    button.secondary{background:#334155}
    button.secondary:hover{background:#475569}
    pre{white-space:pre-wrap;word-break:break-word;background:var(--panel2);border-radius:12px;padding:12px;border:1px solid var(--border);max-height:360px;overflow:auto}
    .ok{color:var(--green)} .warn{color:var(--yellow)} .bad{color:var(--red)} .muted{color:var(--muted)}
    .table{width:100%;border-collapse:collapse;font-size:13px}
    .table th,.table td{border-bottom:1px solid var(--border);padding:8px;text-align:left;vertical-align:top}
    .mono{font-family:Consolas,Menlo,monospace}
    .actions{display:flex;gap:10px;align-items:center;margin-top:10px;margin-bottom:16px;flex-wrap:wrap}
    .chat-shell{display:flex;flex-direction:column;min-height:78vh}
    .chat-messages{flex:1;overflow:auto;display:flex;flex-direction:column;gap:12px;padding-right:4px}
    .msg{max-width:88%;padding:12px 14px;border-radius:16px;border:1px solid var(--border);white-space:pre-wrap;word-break:break-word;line-height:1.45}
    .msg.user{align-self:flex-end;background:#172554}
    .msg.assistant{align-self:flex-start;background:#0b1220}
    .msg.system{align-self:center;background:#1f2937;color:#cbd5e1}
    .msg .role{font-size:12px;color:var(--muted);margin-bottom:6px}
    .chat-bar{margin-top:14px;border-top:1px solid var(--border);padding-top:14px}
    textarea{width:100%;min-height:110px;max-height:220px;resize:vertical;background:var(--panel2);color:var(--text);border:1px solid var(--border);border-radius:12px;padding:12px;font:inherit}
    .controls{display:flex;gap:10px;align-items:center;justify-content:space-between;margin-top:10px;flex-wrap:wrap}
    select{background:var(--panel2);color:var(--text);border:1px solid var(--border);border-radius:10px;padding:10px}
    .toolbar{display:flex;gap:10px;flex-wrap:wrap}
    .section-note{color:var(--muted);font-size:13px}
    @media (max-width:1100px){.layout{grid-template-columns:1fr}.chat-shell{min-height:60vh}}
  </style>
</head>
<body>
<header>
  <h1>Jarvis Operator Panel v3</h1>
  <small id="meta"></small>
</header>
<div class="wrap">
  <div class="layout">
    <div class="col">
      <div class="card">
        <h2>Operator actions</h2>
        <div class="actions">
          <button onclick="reloadAll()">Refresh</button>
          <button class="secondary" onclick="runAction('restart_backend')">Restart backend</button>
          <button class="secondary" onclick="runAction('phase2_latest')">Phase2 latest</button>
          <button class="secondary" onclick="runAction('normalize_latest')">Normalize latest</button>
        </div>
        <div class="section-note">Стабильные operator actions и диагностика.</div>
      </div>
      <div class="card">
        <h2>Probe summary</h2>
        <div id="probeSummary">Loading...</div>
      </div>
      <div class="card">
        <h2>Hardening status</h2>
        <div id="hardening">Loading...</div>
      </div>
      <div class="card">
        <h2>Recent real runs</h2>
        <div id="runs">Loading...</div>
      </div>
      <div class="card">
        <h2>Action result</h2>
        <pre id="actionResult">No actions yet.</pre>
      </div>
    </div>
    <div class="col">
      <div class="card chat-shell">
        <h2>Chat with Jarvis</h2>
        <div class="section-note">Jarvis is online. Говори обычным текстом — панель попробует реальный agent invoke, а потом устойчивый локальный fallback.</div>
        <div id="chatMessages" class="chat-messages"></div>
        <div class="chat-bar">
          <textarea id="chatInput" placeholder="Напиши обычным текстом, что нужно сделать..."></textarea>
          <div class="controls">
            <div class="toolbar">
              <select id="chatMode">
                <option value="default">default</option>
                <option value="operator">operator</option>
                <option value="task">task</option>
              </select>
              <button onclick="sendChat()">Send</button>
              <button class="secondary" onclick="clearChat()">Clear chat</button>
            </div>
            <div class="section-note">Enter = send, Shift+Enter = newline</div>
          </div>
        </div>
      </div>
      <div class="card">
        <h2>Raw summary</h2>
        <pre id="raw">Loading...</pre>
      </div>
    </div>
  </div>
</div>
<script>
(function () {
    "use strict";

    var chatHistory = [];

    function el(id) {
        return document.getElementById(id);
    }

    function esc(value) {
        return String(value == null ? "" : value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
    }

    function setText(id, text) {
        var node = el(id);
        if (node) {
            node.textContent = String(text == null ? "" : text);
        }
    }

    function setHtml(id, html) {
        var node = el(id);
        if (node) {
            node.innerHTML = html;
        }
    }

    function setDebug(where, error) {
        var message = where + ": " + String(error && error.stack ? error.stack : error);
        setText("raw", message);
        setText("actionResult", message);
        try { console.error(message); } catch (e) {}
    }

    async function jget(path) {
        var response = await fetch(path, {
            method: "GET",
            cache: "no-store",
            headers: {
                "Cache-Control": "no-cache"
            }
        });
        if (!response.ok) {
            throw new Error(path + " -> HTTP " + response.status);
        }
        return await response.json();
    }

    async function jpost(path, payload) {
        var response = await fetch(path, {
            method: "POST",
            cache: "no-store",
            headers: {
                "Content-Type": "application/json; charset=utf-8",
                "Cache-Control": "no-cache"
            },
            body: JSON.stringify(payload || {})
        });

        var text = await response.text();
        try {
            return JSON.parse(text);
        } catch (e) {
            return { ok: false, raw: text, status: response.status };
        }
    }

    function pushMessage(role, text) {
        chatHistory.push({
            role: String(role || ""),
            text: String(text == null ? "" : text)
        });
        renderChat();
    }

    function replaceThinking(text) {
        for (var i = chatHistory.length - 1; i >= 0; i--) {
            if (chatHistory[i].role === "assistant" && chatHistory[i].text === "Thinking...") {
                chatHistory[i].text = String(text == null ? "" : text);
                renderChat();
                return;
            }
        }
        pushMessage("assistant", text);
    }

    function renderChat() {
        var root = el("chatMessages");
        if (!root) { return; }

        var html = '';
        html += '<div class="msg system">';
        html += '<div class="role">system</div>';
        html += 'Jarvis is online. Говори обычным текстом — панель попробует реальный agent invoke, а потом устойчивый локальный fallback.';
        html += '</div>';

        for (var i = 0; i < chatHistory.length; i++) {
            var msg = chatHistory[i];
            var cls = msg.role === "user" ? "user" : "assistant";
            html += '<div class="msg ' + cls + '">';
            html += '<div class="role">' + esc(msg.role) + '</div>';
            html += esc(msg.text);
            html += '</div>';
        }

        root.innerHTML = html;
        root.scrollTop = root.scrollHeight;
    }

    async function loadMeta() {
        var data = await jget("/api/meta");
        setText("meta", "project_root=" + data.project_root + " | backend=" + data.backend_base_url + " | panel=" + data.panel_url + " | build=frontfix_v2");
        return data;
    }

    async function loadProbeSummary() {
        var data = await jget("/api/probe/summary");
        var html = "";
        html += '<table class="table">';
        html += '<tr><td>critical_ok</td><td>' + esc(data.critical_ok) + '</td></tr>';
        html += '<tr><td>ok_count</td><td>' + esc(data.ok_count) + '</td></tr>';
        html += '<tr><td>optional_missing_count</td><td>' + esc(data.optional_missing_count) + '</td></tr>';
        html += '<tr><td>bad_count</td><td>' + esc(data.bad_count) + '</td></tr>';
        html += '</table>';
        setHtml("probeSummary", html);
        return data;
    }

    async function loadHardening() {
        var data = await jget("/api/hardening");
        var checks = (data && data.checks) ? data.checks : {};
        var rows = "";
        for (var key in checks) {
            if (!Object.prototype.hasOwnProperty.call(checks, key)) { continue; }
            var val = checks[key];
            var cls = val === true ? "ok" : "bad";
            rows += '<tr><td class="mono">' + esc(key) + '</td><td><span class="' + cls + '">' + esc(val) + '</span></td></tr>';
        }
        setHtml("hardening",
            '<table class="table"><thead><tr><th>Check</th><th>Value</th></tr></thead><tbody>' +
            rows +
            '</tbody></table>'
        );
        return data;
    }

    async function loadRuns() {
        var data = await jget("/api/runs");
        var items = (data && data.items) ? data.items : [];
        if (!items.length) {
            setHtml("runs", '<span class="warn">No real run directories found.</span>');
            return data;
        }

        var html = '<table class="table"><thead><tr><th>Run dir</th><th>Updated</th><th>Files</th></tr></thead><tbody>';
        for (var i = 0; i < items.length; i++) {
            var item = items[i];
            var fileNames = [];
            var files = item.files || [];
            for (var j = 0; j < files.length; j++) {
                fileNames.push(files[j].name + " (" + files[j].size + ")");
            }
            html += '<tr>';
            html += '<td class="mono">' + esc(item.name) + '</td>';
            html += '<td>' + esc(item.updated_at) + '</td>';
            html += '<td>' + esc(fileNames.join(", ")) + '</td>';
            html += '</tr>';
        }
        html += '</tbody></table>';
        setHtml("runs", html);
        return data;
    }

    async function reloadAllImpl() {
        try {
            var meta = await loadMeta();
            var probeSummary = await loadProbeSummary();
            var hardening = await loadHardening();
            var runs = await loadRuns();
            setText("raw", JSON.stringify({
                meta: meta,
                probeSummary: probeSummary,
                hardening: hardening,
                runs: runs
            }, null, 2));
        } catch (err) {
            setDebug("reloadAll", err);
        }
    }

    async function sendChatImpl() {
        try {
            var input = el("chatInput");
            var modeNode = el("chatMode");
            if (!input) {
                throw new Error("chatInput not found");
            }
            var text = String(input.value || "").trim();
            var mode = modeNode ? String(modeNode.value || "default") : "default";
            if (!text) { return; }

            pushMessage("user", text);
            input.value = "";
            pushMessage("assistant", "Thinking...");

            var data = await jpost("/api/chat", {
                message: text,
                mode: mode
            });

            if (data && data.ok) {
                replaceThinking(data.answer || JSON.stringify(data, null, 2));
            } else {
                replaceThinking("Chat debug:\n" + JSON.stringify(data, null, 2));
            }
        } catch (err) {
            replaceThinking("Chat error: " + String(err && err.stack ? err.stack : err));
            setDebug("sendChat", err);
        }
    }

    async function runActionImpl(name) {
        try {
            setText("actionResult", "Running " + name + "...");
            var data = await jpost("/api/actions", { action: name });
            setText("actionResult", JSON.stringify(data, null, 2));
            await reloadAllImpl();
        } catch (err) {
            setDebug("runAction(" + name + ")", err);
        }
    }

    function clearChatImpl() {
        chatHistory = [];
        renderChat();
    }

    window.reloadAll = function () { reloadAllImpl(); return false; };
    window.sendChat  = function () { sendChatImpl(); return false; };
    window.clearChat = function () { clearChatImpl(); return false; };
    window.runAction = function (name) { runActionImpl(name); return false; };

    window.addEventListener("error", function (event) {
        setDebug("window.onerror", event && (event.error || event.message || "Unknown error"));
    });

    window.addEventListener("unhandledrejection", function (event) {
        setDebug("unhandledrejection", event && (event.reason || "Unknown rejection"));
    });

    document.addEventListener("DOMContentLoaded", function () {
        try {
            renderChat();
            var input = el("chatInput");
            if (input) {
                input.addEventListener("keydown", function (e) {
                    if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        sendChatImpl();
                    }
                });
            }
            reloadAllImpl();
        } catch (err) {
            setDebug("DOMContentLoaded", err);
        }
    });
})();
</script>
</body>
</html>
"""


def read_text_best_effort(path: Path) -> str:
    if not path.exists():
        return ""
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1251", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except Exception:
            pass
    return raw.decode("utf-8", errors="replace")


def _extract_items(data):
    if isinstance(data, dict):
        for key in ("items", "agents", "data", "results"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    if isinstance(data, list):
        return data
    return []


def panel_memory_path(project_root: Path) -> Path:
    return project_root / "jarvis_stage3_artifacts" / PANEL_CHAT_MEMORY_FILE


def load_panel_memory(project_root: Path) -> dict:
    path = panel_memory_path(project_root)
    if not path.exists():
        return {"items": []}
    try:
        raw = read_text_best_effort(path)
        data = json.loads(raw) if raw.strip() else {"items": []}
        if not isinstance(data, dict):
            return {"items": []}
        if "items" not in data or not isinstance(data["items"], list):
            data["items"] = []
        return data
    except Exception:
        return {"items": []}


def save_panel_memory(project_root: Path, data: dict) -> None:
    path = panel_memory_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def add_panel_memory(project_root: Path, text: str) -> dict:
    data = load_panel_memory(project_root)
    items = data.get("items", [])
    items.append({
        "text": text,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    data["items"] = items[-200:]
    save_panel_memory(project_root, data)
    return data


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


def safe_probe(base_url: str, path: str, name: str) -> dict:
    url = base_url.rstrip("/") + path
    started = time.time()
    try:
        req = Request(url, headers={"User-Agent": "JarvisOperatorPanel/3.0"})
        with urlopen(req, timeout=6) as resp:
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
    except HTTPError as exc:
        level = "optional_missing" if (exc.code == 404 and path in OPTIONAL_ENDPOINTS) else "bad"
        status_text = "optional_404" if level == "optional_missing" else f"http_{exc.code}"
        return {
            "name": name,
            "path": path,
            "ok": False,
            "level": level,
            "status_code": exc.code,
            "status_text": status_text,
            "elapsed_ms": int((time.time() - started) * 1000),
            "summary": "",
            "error": str(exc),
        }
    except URLError as exc:
        return {
            "name": name,
            "path": path,
            "ok": False,
            "level": "bad",
            "status_code": None,
            "status_text": "unreachable",
            "elapsed_ms": int((time.time() - started) * 1000),
            "summary": "",
            "error": str(exc),
        }
    except Exception as exc:
        return {
            "name": name,
            "path": path,
            "ok": False,
            "level": "bad",
            "status_code": None,
            "status_text": "error",
            "elapsed_ms": int((time.time() - started) * 1000),
            "summary": "",
            "error": str(exc),
        }


def build_probe_summary(items: list[dict]) -> dict:
    ok_count = sum(1 for item in items if item["level"] == "ok")
    optional_missing_count = sum(1 for item in items if item["level"] == "optional_missing")
    bad_count = sum(1 for item in items if item["level"] == "bad")
    critical_ok = all((item["level"] == "ok") or (item["path"] in OPTIONAL_ENDPOINTS) for item in items)
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
    dirs = [path for path in runs_dir.iterdir() if path.is_dir()]
    dirs.sort(key=lambda path: path.stat().st_mtime, reverse=True)
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
        names = ", ".join(file["name"] for file in run.get("files", [])[:6]) or "без файлов"
        lines.append(f"- {run['name']} — обновлён {run['updated_at']}; файлы: {names}")
    return "\n".join(lines)


def summarize_health(base_url: str) -> str:
    items = [safe_probe(base_url, endpoint, name) for endpoint, name in PROBE_ENDPOINTS]
    summary = build_probe_summary(items)
    lines = []
    if summary["critical_ok"]:
        lines.append("Я в хорошем состоянии. Критических проблем сейчас не вижу.")
    else:
        lines.append("Есть несколько узких мест, на которые стоит посмотреть внимательнее.")
    lines.append(
        "По текущему состоянию: стабильных проверок — "
        f"{summary['ok_count']}, необязательных пропусков — {summary['optional_missing_count']}, "
        f"критических ошибок — {summary['bad_count']}."
    )
    bad_items = [item for item in items if item["level"] == "bad"]
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
            match = re.search(r"тво[её]\s+имя\s+([A-Za-zА-Яа-яЁёІіЇїЄєҐґ0-9_-]+)", text, flags=re.I)
            if match:
                jarvis_name = match.group(1).strip()
        if not user_name:
            match = re.search(r"меня\s+зовут\s+([A-ЯA-ZЁІЇЄҐ][^.,;\n]+)", text, flags=re.I)
            if match:
                user_name = match.group(1).strip()
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
            lines.append(f"- {item.get('timestamp', '')} | {item.get('text', '')}")
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
        "panel_server_v3_exists": True,
    }
    return {"checks": checks}


def run_powershell_file(project_root: Path, script_name: str, extra_args: list[str] | None = None) -> dict:
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

    proc = subprocess.run(
        cmd,
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
        "stdout": proc.stdout[-12000:],
        "stderr": proc.stderr[-12000:],
        "script": str(script_path),
    }


def extract_text(obj) -> str:
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, (int, float, bool)):
        return str(obj)
    if isinstance(obj, list):
        return "\n".join(filter(None, (extract_text(item) for item in obj))).strip()
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
        req = Request(openapi_url, headers={"User-Agent": "JarvisOperatorPanel/3.0"})
        with urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        paths = data.get("paths", {})
        if isinstance(paths, dict):
            return list(paths.keys())
    except Exception:
        pass
    return []


def _http_get_json(url: str, timeout: int = 20) -> dict:
    req = Request(url, headers={"User-Agent": "JarvisOperatorPanel/3.0"})
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
            "User-Agent": "JarvisOperatorPanel/3.0",
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
                capabilities = ", ".join(item.get("capabilities", []))
                parts.append(f"- {name} — type={item.get('type')}, capabilities={capabilities}")
    except Exception as exc:
        parts.append(f"Не удалось прочитать adapters: {exc}")

    try:
        registry = get_agents_registry(base_url)
        items = _extract_items(registry)
        if items:
            parts.append("")
            parts.append("И вот какие AI-агенты сейчас есть в registry:")
            for item in items[:10]:
                capabilities = ", ".join(item.get("capabilities", []))
                parts.append(
                    f"- {item.get('display_name') or item.get('name')} ({item.get('agent_id')}) — "
                    f"adapter={item.get('adapter_name')}, capabilities={capabilities}"
                )
    except Exception as exc:
        parts.append(f"Не удалось прочитать registry: {exc}")

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
        capabilities = [str(value).lower() for value in item.get("capabilities", [])]
        adapter = str(item.get("adapter_name") or "").lower()
        name = str(item.get("display_name") or item.get("name") or "").lower()

        score = 0
        if "chat" in capabilities:
            score += 20
        if "reasoning" in capabilities:
            score += 10
        if "ollama" in adapter or "ollama" in name:
            score += 30
        if "local" in adapter or "echo" in adapter or "local" in name or "echo" in name:
            score += 5
        if "openai" in adapter or "claude" in adapter:
            score += 1

        scored.append((score, item))

    scored.sort(key=lambda pair: pair[0], reverse=True)
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
        except Exception as exc:
            attempts.append({"payload": payload, "error": str(exc)})

    return {"ok": False, "reason": "all_payloads_failed", "tried": attempts[-10:]}


def try_agents_invoke(base_url: str, message: str, mode: str) -> dict:
    discovered = discover_backend_routes(base_url)
    if discovered and "/api/agents/invoke" not in discovered:
        return {"ok": False, "reason": "route_missing", "discovered_routes": discovered}

    url = base_url.rstrip("/") + "/api/agents/invoke"
    chosen = choose_chat_agent(base_url)

    base_variants = []
    for key in ("message", "text", "input", "instruction", "task", "prompt", "query", "content"):
        item = {key: message}
        if mode and mode != "default":
            item["mode"] = mode
        base_variants.append(item)

    payloads = list(base_variants)
    if chosen:
        agent_id = chosen.get("agent_id")
        agent_name = chosen.get("display_name") or chosen.get("name")
        adapter_name = chosen.get("adapter_name")

        for base in base_variants:
            if agent_id:
                tmp = dict(base)
                tmp["agent_id"] = agent_id
                payloads.append(tmp)
                payloads.append({"agent_id": agent_id, "payload": base})
            if agent_name:
                tmp = dict(base)
                tmp["agent_name"] = agent_name
                payloads.append(tmp)
            if adapter_name:
                payloads.append({"adapter_name": adapter_name, "payload": base})

    attempts = []
    for payload in payloads:
        try:
            result = _http_json(url, payload, timeout=120)
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
        except Exception as exc:
            attempts.append({"payload": payload, "error": str(exc)})

    return {
        "ok": False,
        "reason": "all_payloads_failed",
        "tried": attempts[-20:],
        "chosen_agent": chosen,
    }


def looks_like_storage_question(text: str) -> bool:
    patterns = [
        r"(куда|где).*(сохран|хран).*(данн|памят)",
        r"куда ты это",
        r"где это хранится",
        r"куда ты сохраняешь данные",
        r"где ты сохраняешь данные",
        r"где хранятся данные",
    ]
    return any(re.search(pattern, text, flags=re.I) for pattern in patterns)


def looks_like_name_question(text: str) -> bool:
    patterns = [
        r"как тебя зовут",
        r"кто ты",
        r"твое имя",
        r"твоё имя",
        r"как твое имя",
        r"как твоё имя",
    ]
    return any(re.search(pattern, text, flags=re.I) for pattern in patterns)


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
    return any(re.search(pattern, text, flags=re.I) for pattern in patterns)


def local_first_intent(text: str) -> bool:
    lower = text.lower().strip()
    if re.fullmatch(r"(привет|здравствуй|hello|hi)\W*", lower):
        return True
    checks = [
        looks_like_storage_question(lower),
        looks_like_name_question(lower),
        looks_like_services_question(lower),
        any(token in lower for token in [
            "что ты умеешь", "как ты себя чувствуешь", "как дела", "статус",
            "health", "status", "гугл диск", "google drive", "гугл консоль",
            "google console", "api ключ", "api keys", "покажи runs", "real run",
            "phase2", "normalize", "перезапусти backend", "как меня зовут",
            "что ты помнишь", "запомни", "remember",
        ]),
    ]
    return any(checks)


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


def operator_chat_fallback(project_root: Path, base_url: str, message: str, mode: str, backend_result: dict | None = None) -> dict:
    lower = message.lower().strip()
    data = load_panel_memory(project_root)
    items = data.get("items", [])

    jarvis_name = "Jarvis"
    user_name = ""
    for item in reversed(items):
        text = str(item.get("text", ""))
        if jarvis_name == "Jarvis":
            match = re.search(r"тво[её]\s+имя\s+([A-Za-zА-Яа-яЁёІіЇїЄєҐґ0-9_-]+)", text, flags=re.I)
            if match:
                jarvis_name = match.group(1).strip()
        if not user_name:
            match = re.search(r"меня\s+зовут\s+([A-ЯA-ZЁІЇЄҐ][^.,;\n]+)", text, flags=re.I)
            if match:
                user_name = match.group(1).strip()
        if jarvis_name != "Jarvis" and user_name:
            break

    if re.fullmatch(r"(привет|здравствуй|hello|hi)\W*", lower):
        answer = f"{jarvis_name} на связи. Рад тебя видеть."
        if user_name:
            answer += f"\nОбращаюсь к тебе как к {user_name}."
        answer += "\nМожешь спросить меня про состояние системы, память панели, сервисы, агентов или попросить operator action."
        return {"ok": True, "source": "fallback_greeting", "answer": answer}

    if any(token in lower for token in ["как ты себя чувствуешь", "как дела", "здоров", "health", "status", "статус"]):
        answer = f"{jarvis_name} на связи.\n\n" + summarize_health(base_url)
        return {"ok": True, "source": "fallback_health", "answer": answer}

    if looks_like_name_question(lower):
        answer = f"Меня зовут {jarvis_name}."
        if user_name:
            answer += f"\nА тебя я помню как {user_name}."
        else:
            answer += "\nЕсли хочешь, я могу запомнить, как к тебе обращаться."
        return {"ok": True, "source": "fallback_name", "answer": answer}

    if any(token in lower for token in ["что ты умеешь", "что умеешь", "what can you do", "help", "помощь", "что ты сейчас умеешь"]):
        answer = (
            f"{jarvis_name} сейчас умеет следующее:\n"
            "- следить за состоянием backend и панели;\n"
            "- показывать health, probes, hardening и последние real runs;\n"
            "- выполнять operator actions: restart backend, Phase2 latest, normalize latest;\n"
            "- запоминать локальные заметки через панель;\n"
            "- показывать доступные сервисы, адаптеры и AI-агентов;\n"
            "- пытаться отвечать через реальный /api/agents/invoke, а если это не удаётся — переходить на устойчивый локальный fallback."
        )
        return {"ok": True, "source": "fallback_help", "answer": answer}

    if looks_like_services_question(lower):
        return {"ok": True, "source": "fallback_services", "answer": summarize_services_and_agents(base_url)}

    if looks_like_storage_question(lower):
        memory_path = project_root / "jarvis_stage3_artifacts" / "panel_chat_memory.json"
        answer = (
            "Я храню записи этого panel-чата в локальной памяти панели.\n\n"
            f"Путь: {memory_path}\n\n"
            "То есть сейчас это локальный файл внутри jarvis_stage3_artifacts. "
            "Backend memory route есть, но как основной контракт для этого panel-чата он пока ещё не закреплён."
        )
        return {"ok": True, "source": "fallback_memory_location", "answer": answer}

    if any(token in lower for token in ["гугл диск", "google drive", "доступ к диску", "доступ к моему гугл диску"]):
        answer = (
            "Прямого подтверждения доступа к Google Drive у меня сейчас нет. "
            "Я вижу рабочие backend routes для memory, approvals, agents, tools и artifacts, "
            "но не вижу отдельного подтверждённого drive-chat-контракта в panel-слое."
        )
        return {"ok": True, "source": "fallback_google_drive_status", "answer": answer}

    if any(token in lower for token in ["гугл консоль", "google console", "api ключ", "api keys", "api key", "подключать возможности", "подключать ключи", "включать openai", "claude", "ollama", "ии с которыми ты умеешь работать"]):
        answer = (
            "Да, внешние возможности Jarvis уже можно возвращать — поэтапно.\n\n"
            "Сейчас база уже есть: adapters, registry, agents invoke, memory, tools и artifacts. "
            "Следующий правильный шаг — закрепить chat-layer через agents invoke, затем аккуратно включать внешние AI-провайдеры, "
            "а уже после этого расширять Google Console и остальные интеграции."
        )
        return {"ok": True, "source": "fallback_capabilities_rollout", "answer": answer}

    if any(token in lower for token in ["покажи ран", "покажи runs", "latest runs", "артефакт", "артефакты", "последние ран", "real run"]):
        return {"ok": True, "source": "fallback_runs", "answer": summarize_runs(project_root)}

    if "перезапусти backend" in lower or "restart backend" in lower:
        result = run_powershell_file(project_root, "restart_backend.ps1")
        text = "Перезапуск backend выполнен." if result.get("ok") else "Перезапуск backend завершился с ошибкой."
        answer = text + "\n\nstdout:\n" + (result.get("stdout", "")[-1000:] or "") + "\n\nstderr:\n" + (result.get("stderr", "")[-1000:] or "")
        return {"ok": True, "source": "fallback_restart_backend", "answer": answer}

    if "phase2" in lower:
        latest = find_latest_run_dir(project_root)
        if not latest:
            return {"ok": True, "source": "fallback_phase2", "answer": "Не нашёл доступных real runs для Phase2."}
        result = run_powershell_file(project_root, "jarvis_sop_phase2_finalize.ps1", ["-ProjectRoot", str(project_root)])
        text = "Phase2 latest выполнен." if result.get("ok") else "Phase2 latest завершился с ошибкой."
        answer = text + f"\nlatest_run={latest}\n\nstdout:\n" + (result.get("stdout", "")[-1200:] or "") + "\n\nstderr:\n" + (result.get("stderr", "")[-1200:] or "")
        return {"ok": True, "source": "fallback_phase2", "answer": answer}

    if "normalize" in lower:
        latest = find_latest_run_dir(project_root)
        if not latest:
            return {"ok": True, "source": "fallback_normalize", "answer": "Не нашёл real run для нормализации."}
        normalize_py = project_root / "scripts" / "jarvis_artifact_normalize_utf8.py"
        pyexe = project_root / ".venv" / "Scripts" / "python.exe"
        python_cmd = str(pyexe if pyexe.exists() else "python")
        proc = subprocess.run(
            [
                python_cmd,
                str(normalize_py),
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
        answer = "Нормализация latest run выполнена." if proc.returncode == 0 else "Нормализация latest run завершилась с ошибкой."
        answer += f"\nlatest_run={latest}\n\nstdout:\n{proc.stdout[-1200:]}\n\nstderr:\n{proc.stderr[-1200:]}"
        return {"ok": True, "source": "fallback_normalize", "answer": answer}

    if any(token in lower for token in ["запомни", "remember"]):
        remembered = extract_remember_text(message)
        add_panel_memory(project_root, remembered)
        backend_store = try_backend_memory_remember(base_url, remembered)
        answer = f"Хорошо. Я запомнил это локально: {remembered}."
        if backend_store.get("ok"):
            answer += "\nЗапись также успешно ушла в backend memory."
        else:
            answer += "\nПока это гарантированно сохранено в локальной памяти панели; backend memory route ещё нужно довести отдельно."
        return {"ok": True, "source": "fallback_memory_write", "answer": answer}

    if any(token in lower for token in ["как меня зовут", "кто я", "что ты помнишь", "что ты запомнил", "remembered", "memory"]):
        if "как меня зовут" in lower and user_name:
            answer = f"Тебя зовут {user_name}."
            if jarvis_name:
                answer += f"\nА меня ты попросил называть {jarvis_name}."
            return {"ok": True, "source": "fallback_memory_read", "answer": answer}
        return {"ok": True, "source": "fallback_memory_read", "answer": summarize_memory(project_root)}

    if re.search(r"^(кто|что|сколько|почему|зачем|как)\b", lower):
        answer = (
            f"{jarvis_name} на связи.\n\n"
            "Это уже похоже на общий разговорный или энциклопедический вопрос. "
            "Сейчас панель лучше всего работает как operator/chat fallback для Jarvis-системы. "
            "Чтобы стабильно отвечать на такие вопросы широко и умно, нужен закреплённый AI chat-layer через backend."
        )
        return {"ok": True, "source": "fallback_general_limit", "answer": answer}

    answer = (
        f"{jarvis_name} на связи.\n\n"
        "Я понял запрос, но сейчас backend всё ещё не даёт мне полноценный general-purpose chat endpoint. "
        "Поэтому я работаю через /api/agents/invoke, а если он не отвечает подходящим образом — перехожу на локальный operator fallback.\n\n"
        "Лучше всего сейчас у меня работают запросы про состояние системы, память панели, сервисы, агентов, последние runs, Phase2, normalize и restart backend."
    )
    return {"ok": True, "source": "fallback_info", "answer": answer}


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

            def _send_html(self, html: str):
                body = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _read_json_body(self) -> dict:
                length = int(self.headers.get("Content-Length", "0") or "0")
                raw = self.rfile.read(length) if length > 0 else b"{}"
                try:
                    return json.loads(raw.decode("utf-8", errors="replace"))
                except Exception:
                    return {}

            def do_GET(self):
                parsed = urlparse(self.path)
                path = parsed.path

                if path == "/":
                    self._send_html(HTML)
                    return

                if path == "/api/meta":
                    self._send_json({
                        "project_root": str(app.project_root),
                        "backend_base_url": app.backend_base_url,
                        "panel_url": f"http://{app.host}:{app.port}/",
                    })
                    return

                if path == "/api/probe":
                    items = [safe_probe(app.backend_base_url, endpoint, name) for endpoint, name in PROBE_ENDPOINTS]
                    self._send_json({"items": items})
                    return

                if path == "/api/probe/summary":
                    items = [safe_probe(app.backend_base_url, endpoint, name) for endpoint, name in PROBE_ENDPOINTS]
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

                self._send_json({"ok": False, "error": "not_found", "path": path}, status=404)

            def do_POST(self):
                parsed = urlparse(self.path)
                path = parsed.path

                if path == "/api/actions":
                    payload = self._read_json_body()
                    action = str(payload.get("action", "")).strip()

                    if action == "restart_backend":
                        result = run_powershell_file(app.project_root, "restart_backend.ps1")
                        self._send_json({"ok": result["ok"], "action": action, "result": result}, status=(200 if result["ok"] else 500))
                        return

                    if action == "phase2_latest":
                        latest = find_latest_run_dir(app.project_root)
                        if not latest:
                            self._send_json({"ok": False, "action": action, "error": "no real runs found"}, status=404)
                            return
                        result = run_powershell_file(app.project_root, "jarvis_sop_phase2_finalize.ps1", ["-ProjectRoot", str(app.project_root)])
                        self._send_json({"ok": result["ok"], "action": action, "latest_run": str(latest), "result": result}, status=(200 if result["ok"] else 500))
                        return

                    if action == "normalize_latest":
                        latest = find_latest_run_dir(app.project_root)
                        if not latest:
                            self._send_json({"ok": False, "action": action, "error": "no real runs found"}, status=404)
                            return
                        normalize_py = app.project_root / "scripts" / "jarvis_artifact_normalize_utf8.py"
                        pyexe = app.project_root / ".venv" / "Scripts" / "python.exe"
                        python_cmd = str(pyexe if pyexe.exists() else "python")
                        proc = subprocess.run(
                            [
                                python_cmd,
                                str(normalize_py),
                                "--run-dir", str(latest),
                                "--recursive",
                                "--pattern", "*.md",
                                "--pattern", "*.txt",
                                "--pattern", "*.json",
                                "--pattern", "*.log",
                            ],
                            cwd=str(app.project_root),
                            capture_output=True,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                            timeout=180,
                        )
                        result = {
                            "ok": proc.returncode == 0,
                            "returncode": proc.returncode,
                            "stdout": proc.stdout[-12000:],
                            "stderr": proc.stderr[-12000:],
                            "latest_run": str(latest),
                        }
                        self._send_json({"ok": result["ok"], "action": action, "result": result}, status=(200 if result["ok"] else 500))
                        return

                    self._send_json({"ok": False, "error": "unknown_action", "action": action}, status=400)
                    return

                if path == "/api/chat":
                    payload = self._read_json_body()
                    message = str(payload.get("message", "")).strip()
                    mode = str(payload.get("mode", "default")).strip()

                    if not message:
                        self._send_json({"ok": False, "error": "empty_message"}, status=400)
                        return

                    if local_first_intent(message):
                        fallback = operator_chat_fallback(app.project_root, app.backend_base_url, message, mode, backend_result=None)
                        self._send_json({
                            "ok": True,
                            "mode": mode,
                            "answer": fallback.get("answer", ""),
                            "debug": {
                                "source": fallback.get("source"),
                                "route_strategy": "local_first",
                            },
                        })
                        return

                    result = backend_chat_request(app.backend_base_url, message, mode)
                    if result.get("ok"):
                        self._send_json({
                            "ok": True,
                            "mode": mode,
                            "answer": result.get("normalized_text", ""),
                            "debug": {
                                "source": "backend_chat_request",
                                "payload_used": result.get("payload_used"),
                                "route": result.get("route"),
                                "transport": result.get("transport"),
                                "chosen_agent": result.get("chosen_agent"),
                            },
                        })
                        return

                    fallback = operator_chat_fallback(app.project_root, app.backend_base_url, message, mode, backend_result=result)
                    self._send_json({
                        "ok": True,
                        "mode": mode,
                        "answer": fallback.get("answer", ""),
                        "debug": {
                            "source": fallback.get("source"),
                            "backend_bridge_error": result.get("error"),
                            "discovered_routes": result.get("discovered_routes", []),
                            "chosen_agent": result.get("chosen_agent"),
                            "route_strategy": "backend_then_fallback",
                        },
                    })
                    return

                self._send_json({"ok": False, "error": "not_found", "path": path}, status=404)

        return Handler


def main():
    parser = argparse.ArgumentParser(description="Jarvis Operator Panel v3")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--backend-base-url", default="http://127.0.0.1:8015")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8026)
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    app = App(
        project_root=project_root,
        backend_base_url=args.backend_base_url,
        host=args.host,
        port=args.port,
    )
    server = ThreadingHTTPServer((args.host, args.port), app.handler())

    print(json.dumps({
        "ok": True,
        "message": "Jarvis Operator Panel v3 started",
        "panel_url": f"http://{args.host}:{args.port}/",
        "backend_base_url": args.backend_base_url,
        "project_root": str(project_root),
    }, ensure_ascii=False, indent=2))

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()