from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

BACKEND = "http://127.0.0.1:8015"
ROOT = Path.cwd()
RUN_DIR = ROOT / "jarvis_stage3_artifacts" / "night_mode_v1" / ("run_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
RUN_DIR.mkdir(parents=True, exist_ok=True)


def save(name: str, data: Dict[str, Any]) -> str:
    path = RUN_DIR / name
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def api(method: str, path: str, body: Dict[str, Any] | None = None, timeout: int = 240) -> Dict[str, Any]:
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(BACKEND + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw else {}


def safe_call(name: str, method: str, path: str, body: Dict[str, Any] | None = None, timeout: int = 240):
    try:
        result = api(method, path, body, timeout)
        save(name + ".json", {"ok": True, "result": result})
        return result
    except Exception as e:
        err = {"ok": False, "error": str(e), "path": path}
        save(name + "_error.json", err)
        return err


def main():
    summary = {
        "ok": True,
        "mode": "night_mode_v1_safe",
        "started_at": datetime.now().isoformat(),
        "run_dir": str(RUN_DIR),
        "actions": []
    }

    health = {
        "backend": safe_call("health_backend", "GET", "/health", timeout=60),
        "internet": safe_call("health_internet", "GET", "/api/jarvis/tools/internet/health", timeout=60),
        "brain": safe_call("health_brain", "GET", "/api/jarvis/brain/health", timeout=60),
        "ai_engineer": safe_call("health_ai_engineer", "GET", "/api/jarvis/ai-engineer/health", timeout=60),
        "telegram_tools": safe_call("health_telegram_tools", "GET", "/api/jarvis/telegram-tools/health", timeout=60),
        "content_async": safe_call("health_content_async", "GET", "/api/jarvis/v5/content-factory/async-health", timeout=60),
    }
    summary["health"] = health
    summary["actions"].append("Health checked")

    task = (
        "Analyze Jarvis current architecture and propose the best safe improvements for: "
        "Telegram control UX, internet agents, table/file delivery, n8n workflows, image generation quality, "
        "provider fallback, observability, self-healing, and Night Mode. "
        "Return practical next steps, risks, and safe PowerShell implementation priorities."
    )

    engineer = safe_call(
        "ai_engineer_review",
        "POST",
        "/api/jarvis/ai-engineer/review",
        {"task": task, "mode": "safe_plan"},
        timeout=300
    )
    summary["actions"].append("AI Engineer review completed")

    research_table = safe_call(
        "night_research_table",
        "POST",
        "/api/jarvis/telegram-tools/internet-table",
        {
            "query": "best practices autonomous AI agent night mode self healing n8n telegram automation observability 2026",
            "max_results": 10,
            "send_to_telegram": True
        },
        timeout=300
    )
    summary["actions"].append("Internet table created and sent to Telegram if configured")

    brain = safe_call(
        "brain_plan",
        "POST",
        "/api/jarvis/brain/plan",
        {
            "task": "Create a safe next-step plan for Jarvis Night Mode Evolution and Full Creator roadmap",
            "quality_target": "research"
        },
        timeout=300
    )
    summary["actions"].append("Brain plan completed")

    final = {
        "summary": summary,
        "engineer": engineer,
        "research_table": research_table,
        "brain": brain,
        "finished_at": datetime.now().isoformat(),
        "safe_to_auto_apply": False,
        "operator_note": "Night Mode V1 only analyzes, creates reports, and sends files. It does not modify code automatically."
    }

    save("night_mode_summary.json", final)
    print(json.dumps(final, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()