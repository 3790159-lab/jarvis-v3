from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

REQUIRED_AGENTS = [
    {
        "name": "planner_agent",
        "role": "Planner",
        "enabled": True,
        "capabilities": ["plan", "route", "echo"],
    },
    {
        "name": "executor_agent",
        "role": "Executes standard file and utility tasks",
        "enabled": True,
        "capabilities": ["echo", "write_file", "read_file", "list_dir", "run_python_tests"],
    },
    {
        "name": "critic_agent",
        "role": "Critic",
        "enabled": True,
        "capabilities": ["critic_check", "echo"],
    },
    {
        "name": "shell_agent",
        "role": "Runs controlled shell commands",
        "enabled": True,
        "capabilities": ["run_shell_command"],
    },
]


def _now_ts() -> float:
    return time.time()


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        broken = path.with_suffix(path.suffix + ".broken")
        broken.write_text(raw, encoding="utf-8")
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_required_agents(state_dir: str | Path) -> dict[str, Any]:
    state_path = Path(state_dir)
    state_path.mkdir(parents=True, exist_ok=True)

    agents_file = state_path / "agents.json"
    current = _read_json(agents_file, {"agents": []})

    if isinstance(current, list):
        container = {"agents": current}
    elif isinstance(current, dict) and isinstance(current.get("agents"), list):
        container = current
    else:
        container = {"agents": []}

    agents_list = container.get("agents", [])
    by_name: dict[str, dict[str, Any]] = {}

    for item in agents_list:
        if isinstance(item, dict):
            name = item.get("name") or item.get("agent_name") or item.get("agent_id")
            if name:
                by_name[str(name)] = item

    added = []
    updated = []

    for required in REQUIRED_AGENTS:
        name = required["name"]
        now = _now_ts()

        if name not in by_name:
            new_item = dict(required)
            new_item["registered_at"] = now
            agents_list.append(new_item)
            by_name[name] = new_item
            added.append(name)
            continue

        item = by_name[name]
        changed = False

        if item.get("enabled") is not True:
            item["enabled"] = True
            changed = True

        if not item.get("role"):
            item["role"] = required["role"]
            changed = True

        caps = item.get("capabilities")
        if not isinstance(caps, list):
            item["capabilities"] = list(required["capabilities"])
            changed = True
        else:
            merged = list(dict.fromkeys([*caps, *required["capabilities"]]))
            if merged != caps:
                item["capabilities"] = merged
                changed = True

        if changed:
            item["updated_at"] = now
            updated.append(name)

    container["agents"] = agents_list
    _write_json(agents_file, container)

    return {
        "status": "ok",
        "agents_file": str(agents_file.resolve()),
        "added": added,
        "updated": updated,
        "enabled_agents": sorted([
            item["name"]
            for item in agents_list
            if isinstance(item, dict) and item.get("name") and item.get("enabled", True)
        ]),
    }
