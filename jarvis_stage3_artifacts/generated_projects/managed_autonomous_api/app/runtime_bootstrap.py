from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

from app.agent_bootstrap import ensure_required_agents

DEFAULT_BASE_URL = "http://127.0.0.1:8010"


def _read_agents(state_dir: str | Path) -> list[dict]:
    agents_file = Path(state_dir) / "agents.json"
    data = json.loads(agents_file.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("agents"), list):
        return [x for x in data["agents"] if isinstance(x, dict)]
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def _post_json(url: str, payload: dict, timeout: float = 5.0) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
        if not raw.strip():
            return {}
        return json.loads(raw)


def _get_json(url: str, timeout: float = 5.0) -> dict:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
        if not raw.strip():
            return {}
        return json.loads(raw)


def bootstrap_runtime_agents(state_dir: str | Path = "state", base_url: str = DEFAULT_BASE_URL) -> dict:
    ensure_result = ensure_required_agents(state_dir)
    agents = _read_agents(state_dir)

    health = _get_json(f"{base_url}/health")
    registered = []
    failed = []

    for agent in agents:
        if not agent.get("enabled", True):
            continue

        payload = {
            "name": agent.get("name"),
            "role": agent.get("role", ""),
            "capabilities": agent.get("capabilities", []),
            "enabled": True,
        }

        try:
            _post_json(f"{base_url}/agents/register", payload)
            registered.append(payload["name"])
        except urllib.error.HTTPError as exc:
            failed.append({"name": payload["name"], "error": f"HTTP {exc.code}"})
        except Exception as exc:
            failed.append({"name": payload["name"], "error": str(exc)})

    return {
        "status": "ok" if not failed else "partial",
        "health": health,
        "ensured_agents": ensure_result.get("enabled_agents", []),
        "registered_runtime_agents": registered,
        "failed": failed,
    }


if __name__ == "__main__":
    state_dir = sys.argv[1] if len(sys.argv) > 1 else "state"
    base_url = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_BASE_URL
    result = bootstrap_runtime_agents(state_dir=state_dir, base_url=base_url)
    print(json.dumps(result, ensure_ascii=False, indent=2))
