"""Jarvis Web Dashboard — Phase 40.

Routes (all under /dashboard):
  GET  /dashboard           — dark HTML dashboard
  GET  /dashboard/api/status — live status JSON
  POST /dashboard/api/chat  — send query to Jarvis
  WS   /dashboard/ws        — real-time push every 5s

Auth: ?chat_id=<TELEGRAM_ALLOWED_CHAT_ID> or cookie jarvis_chat_id
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter(tags=["jarvis-dashboard"])

_ROOT = Path(__file__).parent.parent.parent
_START_TIME = time.time()

# ── Status cache (10s TTL) — prevents file reads every 5s per WS client ──────
_status_cache: Optional[Dict[str, Any]] = None
_status_cache_ts: float = 0.0
_STATUS_TTL = 10.0

# ── WebSocket connection counter ──────────────────────────────────────────────
_ws_connections: int = 0
_WS_MAX_CONNECTIONS = 10

# ── Auth helper ───────────────────────────────────────────────────────────────

def _allowed_chat_id() -> str:
    return str(os.getenv("TELEGRAM_ALLOWED_CHAT_ID", "")).strip()


def _is_authorized(request: Request) -> bool:
    allowed = _allowed_chat_id()
    if not allowed:
        return True  # open if not configured
    chat_id = (
        request.query_params.get("chat_id")
        or request.cookies.get("jarvis_chat_id")
        or ""
    )
    return chat_id.strip() == allowed


# ── Status builder ────────────────────────────────────────────────────────────

def _get_status_cached() -> Dict[str, Any]:
    """Return cached status, refreshing if TTL expired."""
    global _status_cache, _status_cache_ts
    now = time.time()
    if _status_cache is None or (now - _status_cache_ts) > _STATUS_TTL:
        _status_cache = _get_status()
        _status_cache_ts = now
    return _status_cache


def _get_status() -> Dict[str, Any]:
    uptime = int(time.time() - _START_TIME)
    agents = {
        "perplexity": bool(os.getenv("PERPLEXITY_API_KEY") or os.getenv("PPLX_API_KEY")),
        "openai": bool(os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY")),
        "anthropic": bool(os.getenv("ANTHROPIC_API_KEY")),
        "replicate": bool(os.getenv("REPLICATE_API_KEY")),
        "n8n": bool(os.getenv("N8N_API_KEY")),
    }

    scheduled_count = 0
    try:
        tp = _ROOT / "state" / "scheduled_tasks.json"
        if tp.exists():
            tasks = json.loads(tp.read_text(encoding="utf-8"))
            scheduled_count = len([t for t in tasks if t.get("active")])
    except Exception:
        pass

    decisions_today = 0
    try:
        dp = _ROOT / "state" / "decisions.jsonl"
        if dp.exists():
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            for line in dp.read_text(encoding="utf-8").splitlines():
                try:
                    rec = json.loads(line)
                    if rec.get("timestamp", "").startswith(today):
                        decisions_today += 1
                except Exception:
                    pass
    except Exception:
        pass

    errors_today = 0
    try:
        ep = _ROOT / "state" / "errors.log"
        if ep.exists():
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            for line in ep.read_text(encoding="utf-8").splitlines():
                try:
                    rec = json.loads(line)
                    if rec.get("timestamp", "").startswith(today):
                        errors_today += 1
                except Exception:
                    pass
    except Exception:
        pass

    return {
        "status": "online",
        "uptime_seconds": uptime,
        "uptime_human": _fmt_uptime(uptime),
        "agents": {k: ("ok" if v else "not_configured") for k, v in agents.items()},
        "scheduled_tasks": scheduled_count,
        "decisions_today": decisions_today,
        "errors_today": errors_today,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _fmt_uptime(s: int) -> str:
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60}s"
    h = s // 3600
    m = (s % 3600) // 60
    return f"{h}h {m}m"


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/dashboard/autonomy")
async def autonomy_dashboard(request: Request):
    """Night Autonomy dashboard — Block H5.8."""
    if not _is_authorized(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        from app.services.night_workflows import NightWorkflow, get_current_phase, PHASES
        from app.services.daily_recap import get_recap
        from app.services.auto_content import get_scheduled_posts, format_scheduled_posts_summary
        from app.services.self_improvement import SelfImprovementLoop
        from app.services.trend_analyzer import get_latest_insights

        wf = NightWorkflow()
        current_phase = get_current_phase()
        status = wf.get_status()
        recap = get_recap()
        scheduled_posts = get_scheduled_posts()
        loop = SelfImprovementLoop()
        improve_stats = loop.get_improvement_stats()
        insights = get_latest_insights()

        return JSONResponse({
            "current_phase": current_phase,
            "phases": {
                name: {
                    "hours": f"{cfg['start']:02d}:00 – {cfg['end']:02d}:00",
                    "label": cfg["label"],
                    "active": name == current_phase,
                }
                for name, cfg in PHASES.items()
            },
            "last_recap": recap,
            "scheduled_posts_count": len(scheduled_posts),
            "scheduled_posts_summary": format_scheduled_posts_summary(scheduled_posts),
            "self_improvement": improve_stats,
            "latest_trends_date": (insights or {}).get("date"),
            "latest_food_trends": (insights or {}).get("food_trends", [])[:5],
        })
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/dashboard/autonomy/html")
async def autonomy_dashboard_html(request: Request):
    """Night Autonomy HTML dashboard page."""
    if not _is_authorized(request):
        return HTMLResponse("<h1>Unauthorized</h1>", status_code=401)
    html = _build_autonomy_html()
    return HTMLResponse(html)


def _build_autonomy_html() -> str:
    return """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Jarvis — Night Autonomy</title>
<style>
body { background: #0d1117; color: #e6edf3; font-family: monospace; margin: 0; padding: 20px; }
h1 { color: #58a6ff; } h2 { color: #79c0ff; border-bottom: 1px solid #30363d; padding-bottom: 8px; }
.card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; margin: 12px 0; }
.phase { display: inline-block; padding: 4px 12px; border-radius: 4px; margin: 4px; font-size: 12px; }
.phase.active { background: #238636; color: #aff5b4; }
.phase.inactive { background: #21262d; color: #8b949e; }
.stat { display: inline-block; min-width: 120px; text-align: center; padding: 12px; margin: 8px; background: #21262d; border-radius: 6px; }
.stat .value { font-size: 24px; color: #58a6ff; } .stat .label { font-size: 11px; color: #8b949e; }
.trend-tag { display: inline-block; background: #1f3d6e; color: #79c0ff; border-radius: 3px; padding: 2px 8px; margin: 2px; font-size: 12px; }
</style>
</head>
<body>
<h1>🌙 Night Autonomy Dashboard</h1>
<div id="content"><div class="card">Loading...</div></div>
<script>
async function loadData() {
  const r = await fetch('/dashboard/autonomy' + window.location.search);
  const d = await r.json();
  if (d.error) { document.getElementById('content').innerHTML = '<div class="card">❌ ' + d.error + '</div>'; return; }
  let html = '<div class="card"><h2>Current Phase</h2>';
  const cp = d.current_phase || 'none';
  html += '<p style="color:#58a6ff;font-size:20px">' + (cp === 'none' ? '☀️ Daytime (no active phase)' : '🌙 ' + cp) + '</p>';
  html += '<div>';
  for (const [name, info] of Object.entries(d.phases || {})) {
    html += '<span class="phase ' + (info.active ? 'active' : 'inactive') + '">' + info.label + ' ' + info.hours + '</span>';
  }
  html += '</div></div>';
  html += '<div class="card"><h2>Last Recap</h2>';
  const rc = d.last_recap;
  if (rc) {
    html += '<div>';
    html += '<div class="stat"><div class="value">' + (rc.tasks_completed||0) + '</div><div class="label">Tasks</div></div>';
    html += '<div class="stat"><div class="value">' + (rc.photos_generated||0) + '</div><div class="label">Photos</div></div>';
    html += '<div class="stat"><div class="value">' + (rc.positive_feedback||0) + '</div><div class="label">👍</div></div>';
    html += '<div class="stat"><div class="value">' + (rc.negative_feedback||0) + '</div><div class="label">👎</div></div>';
    html += '<div class="stat"><div class="value">' + (rc.errors_count||0) + '</div><div class="label">Errors</div></div>';
    html += '</div><p style="color:#8b949e">Focus: ' + (rc.tomorrow_focus||'') + '</p>';
  } else { html += '<p style="color:#8b949e">No recap yet for today.</p>'; }
  html += '</div>';
  html += '<div class="card"><h2>Scheduled Posts</h2><p>' + (d.scheduled_posts_summary||'None') + '</p></div>';
  html += '<div class="card"><h2>Self-Improvement</h2>';
  const si = d.self_improvement || {};
  html += '<p>Total optimized prompts: <b>' + (si.total_optimized||0) + '</b></p>';
  html += '</div>';
  html += '<div class="card"><h2>Trends</h2>';
  if (d.latest_food_trends && d.latest_food_trends.length) {
    for (const t of d.latest_food_trends) { html += '<span class="trend-tag">' + t + '</span>'; }
  } else { html += '<p style="color:#8b949e">No trends yet. Will be fetched tonight.</p>'; }
  html += '</div>';
  document.getElementById('content').innerHTML = html;
}
loadData();
setInterval(loadData, 30000);
</script>
</body>
</html>"""


@router.get("/dashboard/api/analytics")
async def analytics_api(request: Request, days: int = 7):
    if not _is_authorized(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        from app.services.analytics import (
            get_decisions_timeseries, get_intent_distribution,
            get_success_rate_over_time, estimate_costs, get_performance_metrics,
        )
        return {
            "timeseries": get_decisions_timeseries(days),
            "intent_distribution": get_intent_distribution(days),
            "success_rate": get_success_rate_over_time(days),
            "costs": estimate_costs(days),
            "performance": get_performance_metrics(),
            "period_days": days,
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/dashboard/api/export")
async def export_api(request: Request, format: str = "json", days: int = 7):
    if not _is_authorized(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        from app.services.analytics import (
            get_decisions_timeseries, get_intent_distribution,
            estimate_costs,
        )
        data = {
            "timeseries": get_decisions_timeseries(days),
            "intent_distribution": get_intent_distribution(days),
            "costs": estimate_costs(days),
        }
        if format == "csv":
            import io
            buf = io.StringIO()
            buf.write("date,count\n")
            for row in data["timeseries"]:
                buf.write(f"{row['date']},{row['count']}\n")
            from fastapi.responses import Response
            return Response(
                content=buf.getvalue(),
                media_type="text/csv",
                headers={"Content-Disposition": f"attachment; filename=jarvis_analytics_{days}d.csv"},
            )
        return data
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    if not _is_authorized(request):
        return HTMLResponse("<h1>401 Unauthorized</h1><p>Pass ?chat_id=YOUR_CHAT_ID</p>", status_code=401)
    return HTMLResponse(_DASHBOARD_HTML)


@router.get("/dashboard/api/status")
async def status_api(request: Request):
    if not _is_authorized(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    return _get_status_cached()


@router.post("/dashboard/api/chat")
async def chat_api(request: Request):
    if not _is_authorized(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    query = (body.get("query") or "").strip()
    if not query:
        return JSONResponse({"error": "query required"}, status_code=400)

    # Route via backend classify_message + run internet research
    response_text = await _process_query(query)
    return {"response": response_text, "query": query}


@router.websocket("/dashboard/ws")
async def ws_endpoint(websocket: WebSocket):
    global _ws_connections
    allowed = _allowed_chat_id()
    if allowed:
        chat_id = websocket.query_params.get("chat_id", "")
        if chat_id != allowed:
            await websocket.close(code=4401)
            return

    if _ws_connections >= _WS_MAX_CONNECTIONS:
        await websocket.close(code=4429)
        return

    _ws_connections += 1
    await websocket.accept()
    try:
        while True:
            status = _get_status_cached()
            await websocket.send_json(status)
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        _ws_connections -= 1


async def _process_query(query: str) -> str:
    """Handle chat query: quick_answer first (8s), then research (30s timeout)."""
    loop = asyncio.get_running_loop()

    # 1. Try quick_answer for simple factual questions
    try:
        quick = await asyncio.wait_for(
            loop.run_in_executor(None, _run_quick_answer, query),
            timeout=8.0,
        )
        if quick:
            return quick
    except (asyncio.TimeoutError, Exception):
        pass

    # 2. Fall back to research endpoint
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(None, _run_research, query),
            timeout=30.0,
        )
        return result
    except asyncio.TimeoutError:
        return "⏱ Запрос занял слишком долго (>30s). Попробуй ещё раз или упрости вопрос."
    except Exception as e:
        return f"Ошибка: {e}"


def _run_quick_answer(query: str) -> str | None:
    """Synchronous quick_answer call for executor."""
    try:
        from app.services.quick_answer import is_simple_question, quick_answer
        if is_simple_question(query):
            return quick_answer(query)
    except Exception:
        pass
    return None


def _run_research(query: str) -> str:
    """Synchronous research call for executor."""
    import urllib.request as _req

    backend = (
        os.getenv("BACKEND_BASE_URL")
        or os.getenv("TELEGRAM_BACKEND_URL")
        or "http://127.0.0.1:8010"
    ).rstrip("/")

    payload = json.dumps({"query": query}, ensure_ascii=False).encode()
    req = _req.Request(
        f"{backend}/api/jarvis/tools/internet/research",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with _req.urlopen(req, timeout=28) as resp:
        data = json.loads(resp.read())
    return data.get("answer") or data.get("result") or "Ответ получен, но пустой."


# ── HTML Template ─────────────────────────────────────────────────────────────

_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Jarvis Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #0d1117; color: #c9d1d9; min-height: 100vh; }
  .header { background: linear-gradient(135deg, #1f2937, #111827);
            border-bottom: 1px solid #30363d; padding: 20px 32px;
            display: flex; align-items: center; gap: 16px; }
  .header h1 { font-size: 1.5rem; font-weight: 700; color: #58a6ff; }
  .badge { background: #238636; color: #fff; border-radius: 12px;
           padding: 3px 10px; font-size: 0.75rem; font-weight: 600; }
  .badge.offline { background: #b62324; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
          gap: 16px; padding: 24px 32px 0; }
  .card { background: #161b22; border: 1px solid #30363d; border-radius: 10px;
          padding: 20px; }
  .card-title { font-size: 0.75rem; color: #8b949e; text-transform: uppercase;
                letter-spacing: 0.05em; margin-bottom: 8px; }
  .card-value { font-size: 2rem; font-weight: 700; color: #58a6ff; }
  .card-value.green { color: #3fb950; }
  .card-value.red { color: #f85149; }
  .section { padding: 24px 32px; }
  .section h2 { font-size: 1rem; color: #8b949e; margin-bottom: 16px; }
  .agents-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 10px; }
  .agent-chip { background: #161b22; border: 1px solid #30363d; border-radius: 8px;
                padding: 10px 14px; display: flex; align-items: center; gap: 8px; }
  .dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
  .dot.ok { background: #3fb950; }
  .dot.nc { background: #8b949e; }
  .chat-box { background: #161b22; border: 1px solid #30363d; border-radius: 10px;
              padding: 16px; }
  .chat-messages { height: 260px; overflow-y: auto; margin-bottom: 12px;
                   display: flex; flex-direction: column; gap: 10px; }
  .msg { padding: 10px 14px; border-radius: 8px; max-width: 85%; font-size: 0.9rem; line-height: 1.5; }
  .msg.user { background: #1f6feb; align-self: flex-end; }
  .msg.bot  { background: #21262d; align-self: flex-start; border: 1px solid #30363d; }
  .chat-input { display: flex; gap: 8px; }
  .chat-input input { flex: 1; background: #0d1117; border: 1px solid #30363d;
                      color: #c9d1d9; border-radius: 8px; padding: 10px 14px;
                      font-size: 0.9rem; outline: none; }
  .chat-input input:focus { border-color: #58a6ff; }
  .chat-input button { background: #238636; color: #fff; border: none; border-radius: 8px;
                       padding: 10px 20px; cursor: pointer; font-size: 0.9rem; }
  .chat-input button:hover { background: #2ea043; }
  .chart-container { background: #161b22; border: 1px solid #30363d; border-radius: 10px;
                     padding: 20px; height: 200px; }
  .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  @media(max-width: 640px) { .two-col { grid-template-columns: 1fr; }
    .grid { grid-template-columns: 1fr 1fr; }
    .header { padding: 16px; } .section { padding: 16px; } }
  .ts { font-size: 0.7rem; color: #8b949e; margin-top: 4px; }
</style>
</head>
<body>
<div class="header">
  <h1>⚡ Jarvis V3</h1>
  <span class="badge" id="status-badge">Loading...</span>
  <span class="ts" id="last-update" style="margin-left:auto"></span>
</div>

<div class="grid">
  <div class="card">
    <div class="card-title">Uptime</div>
    <div class="card-value green" id="c-uptime">—</div>
  </div>
  <div class="card">
    <div class="card-title">Решений сегодня</div>
    <div class="card-value" id="c-decisions">—</div>
  </div>
  <div class="card">
    <div class="card-title">Задач в расписании</div>
    <div class="card-value" id="c-tasks">—</div>
  </div>
  <div class="card">
    <div class="card-title">Ошибок сегодня</div>
    <div class="card-value red" id="c-errors">—</div>
  </div>
</div>

<div class="section">
  <h2>Агенты</h2>
  <div class="agents-grid" id="agents-grid"></div>
</div>

<div class="section">
  <div class="two-col">
    <div>
      <h2>Чат с Jarvis</h2>
      <div class="chat-box">
        <div class="chat-messages" id="chat-messages">
          <div class="msg bot">Привет! Я Jarvis. Задай вопрос или дай команду.</div>
        </div>
        <div class="chat-input">
          <input type="text" id="chat-input" placeholder="Спроси что-нибудь..." />
          <button onclick="sendChat()">Отправить</button>
        </div>
      </div>
    </div>
    <div>
      <h2>Активность (решения / час)</h2>
      <div class="chart-container">
        <canvas id="activityChart"></canvas>
      </div>
    </div>
  </div>
</div>

<script>
// ─ WebSocket / polling fallback ────────────────────────────────────────────
const params = new URLSearchParams(window.location.search);
const chatId = params.get('chat_id') || '';
const wsUrl = (location.protocol === 'https:' ? 'wss' : 'ws') +
              '://' + location.host + '/dashboard/ws' + (chatId ? '?chat_id=' + chatId : '');

let ws;
function connectWS() {
  ws = new WebSocket(wsUrl);
  ws.onmessage = e => updateStatus(JSON.parse(e.data));
  ws.onclose = () => setTimeout(connectWS, 3000);
}
connectWS();

function updateStatus(s) {
  document.getElementById('c-uptime').textContent = s.uptime_human;
  document.getElementById('c-decisions').textContent = s.decisions_today;
  document.getElementById('c-tasks').textContent = s.scheduled_tasks;
  document.getElementById('c-errors').textContent = s.errors_today;
  document.getElementById('c-errors').className = 'card-value ' + (s.errors_today > 0 ? 'red' : 'green');
  const badge = document.getElementById('status-badge');
  badge.textContent = s.status === 'online' ? '✅ Online' : '❌ Offline';
  badge.className = 'badge' + (s.status === 'online' ? '' : ' offline');
  document.getElementById('last-update').textContent = 'Обновлено: ' + new Date().toLocaleTimeString();
  renderAgents(s.agents);
}

function renderAgents(agents) {
  const grid = document.getElementById('agents-grid');
  grid.innerHTML = '';
  for (const [name, st] of Object.entries(agents)) {
    const ok = st === 'ok';
    grid.innerHTML += '<div class="agent-chip"><div class="dot ' + (ok ? 'ok' : 'nc') + '"></div>'
      + '<span>' + name + '</span></div>';
  }
}

// ─ Activity chart ──────────────────────────────────────────────────────────
const ctx = document.getElementById('activityChart').getContext('2d');
const labels = Array.from({length: 12}, (_, i) => (new Date(Date.now() - (11-i)*5*60*1000)).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}));
const chartData = new Array(12).fill(0);
const chart = new Chart(ctx, {
  type: 'line',
  data: {
    labels,
    datasets: [{
      label: 'Решения',
      data: chartData,
      borderColor: '#58a6ff',
      backgroundColor: 'rgba(88,166,255,0.1)',
      tension: 0.4,
      fill: true,
      pointRadius: 3,
    }]
  },
  options: {
    responsive: true, maintainAspectRatio: false,
    plugins: { legend: { display: false } },
    scales: {
      x: { ticks: { color: '#8b949e', font: {size: 10} }, grid: { color: '#21262d' } },
      y: { ticks: { color: '#8b949e', stepSize: 1 }, grid: { color: '#21262d' }, min: 0 }
    }
  }
});

// Refresh chart every 30s with latest decision count
let lastDecisions = 0;
setInterval(() => {
  const cur = parseInt(document.getElementById('c-decisions').textContent) || 0;
  chartData.shift(); chartData.push(Math.max(0, cur - lastDecisions));
  labels.shift(); labels.push(new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}));
  lastDecisions = cur;
  chart.update('none');
}, 30000);

// ─ Chat ─────────────────────────────────────────────────────────────────────
async function sendChat() {
  const inp = document.getElementById('chat-input');
  const query = inp.value.trim();
  if (!query) return;
  inp.value = '';
  appendMsg('user', query);
  appendMsg('bot', '...');
  const msgs = document.getElementById('chat-messages');
  const thinkingEl = msgs.lastElementChild;

  const ctrl = new AbortController();
  const tid = setTimeout(() => ctrl.abort(), 35000);
  try {
    const r = await fetch('/dashboard/api/chat' + (chatId ? '?chat_id='+chatId : ''), {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({query}),
      signal: ctrl.signal,
    });
    clearTimeout(tid);
    const d = await r.json();
    thinkingEl.textContent = d.response || d.error || 'Нет ответа';
  } catch(e) {
    clearTimeout(tid);
    thinkingEl.textContent = e.name === 'AbortError'
      ? '⏱ Таймаут (35s). Попробуй ещё раз.'
      : 'Ошибка: ' + e.message;
  }
}

document.getElementById('chat-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') sendChat();
});

function appendMsg(role, text) {
  const div = document.createElement('div');
  div.className = 'msg ' + role;
  div.textContent = text;
  const msgs = document.getElementById('chat-messages');
  msgs.appendChild(div);
  msgs.scrollTop = msgs.scrollHeight;
}
</script>
</body>
</html>
"""
