from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.jarvis_operator_task_center import read_env_file
from app.services.jarvis_truth_guard import JarvisTruthGuard


def load_json(path: Path, default: Any = None) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def tg_call(token: str, method: str, params: dict | None = None) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(params or {}).encode("utf-8") if params else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=45) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def send(token: str, chat_id: str, text: str) -> None:
    chunks = [text[i:i + 3500] for i in range(0, len(text), 3500)] or [text]
    for chunk in chunks:
        tg_call(token, "sendMessage", {
            "chat_id": chat_id,
            "text": chunk,
            "disable_web_page_preview": "true",
        })


def roots() -> List[Path]:
    # V2: notify from high-level task/execution results only.
    # Do NOT scan code_improvement_lane directly to avoid duplicate brainexec + codefix messages.
    candidates = [
        PROJECT_ROOT / "jarvis_stage3_artifacts" / "operator_task_center" / "executions",
        PROJECT_ROOT / "jarvis_stage3_artifacts" / "brain_executor" / "runs",
        PROJECT_ROOT / "jarvis_stage3_artifacts" / "n8n_super_agent" / "runs",
    ]
    return [x for x in candidates if x.exists()]


def collect_files(started_at: float, include_history: bool) -> List[Path]:
    files: List[Path] = []
    for root in roots():
        files.extend(root.glob("*.json"))
        files.extend(root.glob("*/result.json"))

    unique = sorted(set(files), key=lambda p: p.stat().st_mtime, reverse=True)
    if include_history:
        return unique[:80]
    return [p for p in unique if p.stat().st_mtime >= started_at - 2][:80]


def extract_status(data: Dict[str, Any]) -> str:
    for key in ["status", "loop_status"]:
        if data.get(key):
            return str(data[key])
    if isinstance(data.get("execution"), dict):
        return str(data["execution"].get("status") or "unknown")
    return "unknown"


def extract_id(path: Path, data: Dict[str, Any]) -> str:
    for key in ["task_id", "execution_id", "run_id"]:
        if data.get(key):
            return str(data[key])
    if isinstance(data.get("execution"), dict):
        for key in ["execution_id", "task_id", "run_id"]:
            if data["execution"].get(key):
                return str(data["execution"][key])
    return path.stem


def extract_objective(data: Dict[str, Any]) -> str:
    for key in ["objective", "raw_task", "task", "user_task"]:
        if data.get(key):
            return str(data[key])
    if isinstance(data.get("execution"), dict):
        return str(data["execution"].get("raw_task") or "")
    if isinstance(data.get("compiled_task"), dict):
        return str(data["compiled_task"].get("raw_task") or "")
    return ""


def extract_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(data.get("execution"), dict):
        return data["execution"]
    if isinstance(data.get("primary_result"), dict):
        return data["primary_result"]
    return data


def format_msg(task_id: str, objective: str, data: Dict[str, Any], truth: Dict[str, Any]) -> str:
    status = extract_status(data)
    payload = extract_payload(data)

    lines = [
        "✅ Задача завершена",
        "",
        f"ID: {task_id}",
        f"Статус: {status}",
    ]

    if objective:
        lines += ["", f"Задача: {objective[:600]}"]

    if isinstance(payload, dict):
        primary = payload.get("primary_result") if isinstance(payload.get("primary_result"), dict) else payload
        workflow_id = primary.get("workflow_id")
        workflow_kind = primary.get("workflow_kind")
        run_id = primary.get("run_id")
        lane = primary.get("lane")
        sheet_url = truth.get("evidence", {}).get("spreadsheet_url")

        if lane:
            lines.append(f"Lane: {lane}")
        if workflow_id:
            lines.append(f"n8n workflow: {workflow_id}")
        if workflow_kind:
            lines.append(f"Тип workflow: {workflow_kind}")
        if run_id:
            lines.append(f"Run ID: {run_id}")
        if sheet_url:
            lines.append(f"Google Sheet: {sheet_url}")

    if not truth.get("ok"):
        lines += ["", "⚠️ Проверка честности результата:", truth.get("safe_message", "Не хватает подтверждения результата.")]

    lines += ["", "Я сам сообщил о завершении, отдельно спрашивать не нужно."]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--include-history", action="store_true")
    args = parser.parse_args()

    env = read_env_file(PROJECT_ROOT)
    token = env.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = env.get("TELEGRAM_ALLOWED_CHAT_ID") or os.environ.get("TELEGRAM_ALLOWED_CHAT_ID")
    if not token or not chat_id:
        print("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_ALLOWED_CHAT_ID")
        return 1

    runtime = PROJECT_ROOT / "jarvis_stage3_artifacts" / "telegram_task_watcher"
    sent_path = runtime / "sent_notifications_v2.json"
    meta_path = runtime / "watcher_meta_v2.json"
    runtime.mkdir(parents=True, exist_ok=True)

    started_at = time.time()
    sent = load_json(sent_path, default={}) or {}
    write_json(meta_path, {"started_at": started_at, "include_history": args.include_history})

    truth_guard = JarvisTruthGuard(PROJECT_ROOT)
    print("Jarvis Task Completion Watcher v2 started. include_history=", args.include_history)

    while True:
        try:
            for path in collect_files(started_at, args.include_history):
                data = load_json(path)
                if not isinstance(data, dict):
                    continue

                status = extract_status(data)
                if status not in {"completed", "completed_with_warnings", "tested", "failed", "failed_validation"}:
                    continue

                task_id = extract_id(path, data)
                unique_key = f"{task_id}:{path.resolve()}"
                if sent.get(unique_key):
                    continue

                objective = extract_objective(data)
                payload = extract_payload(data)
                truth = truth_guard.guard_summary(objective, payload)

                send(token, chat_id, format_msg(task_id, objective, data, truth))

                sent[unique_key] = {"task_id": task_id, "path": str(path), "status": status, "sent_at": time.time()}
                write_json(sent_path, sent)

            time.sleep(15)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            print(f"watcher error: {exc}")
            time.sleep(20)


if __name__ == "__main__":
    raise SystemExit(main())