from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE_URL = "http://127.0.0.1:8010"


def get_json(url: str, timeout: float = 5.0) -> dict:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
        if not raw.strip():
            return {}
        return json.loads(raw)


def post_json(url: str, payload: dict, timeout: float = 5.0) -> dict:
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


def main() -> int:
    try:
        health = get_json(f"{BASE_URL}/health")
    except Exception as exc:
        print(json.dumps({"status": "error", "stage": "health", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1

    payload = {
        "task_type": "critic_check",
        "payload": {
            "text": "mission_regression_check"
        },
        "assigned_agent": "critic_agent"
    }

    try:
        created = post_json(f"{BASE_URL}/tasks", payload)
    except urllib.error.HTTPError as exc:
        print(json.dumps({"status": "error", "stage": "create_task", "error": f"HTTP {exc.code}"}, ensure_ascii=False, indent=2))
        return 2
    except Exception as exc:
        print(json.dumps({"status": "error", "stage": "create_task", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 3

    time.sleep(3)

    try:
        tasks = get_json(f"{BASE_URL}/tasks")
    except Exception as exc:
        print(json.dumps({"status": "error", "stage": "read_tasks", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 4

    print(json.dumps({
        "status": "ok",
        "health": health,
        "created_task": created,
        "tasks_snapshot": tasks,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
