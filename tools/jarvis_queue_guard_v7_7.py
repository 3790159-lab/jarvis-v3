import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path


def now():
    return datetime.now().isoformat(timespec="seconds")


def read_json(path, default):
    try:
        p = Path(path)
        if not p.exists():
            return default
        text = p.read_text(encoding="utf-8-sig", errors="replace")
        return json.loads(text)
    except Exception:
        return default


def write_json(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_task(t):
    if not isinstance(t, dict):
        return None
    tid = t.get("id")
    if not tid:
        return None
    t.setdefault("status", "pending")
    t.setdefault("priority", 50)
    t.setdefault("risk", "low")
    t.setdefault("lane", "unknown")
    t.setdefault("created_at", now())
    return t


def extract_queue_from_snapshot(data):
    if not isinstance(data, dict):
        return []
    q = data.get("queue")
    if isinstance(q, dict) and isinstance(q.get("items"), list):
        return q["items"]
    if isinstance(data.get("items"), list):
        return data["items"]
    return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--restore", action="store_true")
    args = ap.parse_args()

    root = Path(args.project_root)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    queue_path = root / "state" / "jarvis_brain" / "action_queue_v6_4.json"
    backup_dir = out / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    if queue_path.exists():
        backup_path = backup_dir / ("action_queue_before_guard_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".json")
        shutil.copy2(queue_path, backup_path)
    else:
        backup_path = None

    current = read_json(queue_path, {"schema": "jarvis.action_queue.v6_4", "items": []})
    current_items = current.get("items", []) if isinstance(current, dict) else []

    sources = []
    candidates = []

    # Current queue first
    for t in current_items:
        nt = normalize_task(t)
        if nt:
            candidates.append(("current", nt))

    # Brain snapshots usually contain the full queue
    brain_root = root / "jarvis_stage3_artifacts" / "strategic_brain_v6_4"
    for p in brain_root.rglob("*.json") if brain_root.exists() else []:
        data = read_json(p, {})
        items = extract_queue_from_snapshot(data)
        if items:
            sources.append(str(p))
            for t in items:
                nt = normalize_task(t)
                if nt:
                    candidates.append((str(p), nt))

    # Merge by id, prefer current status/history when available
    merged = {}
    source_map = {}
    for src, task in candidates:
        tid = task["id"]
        if tid not in merged:
            merged[tid] = task
            source_map[tid] = src
        else:
            old = merged[tid]
            # Preserve completed/failed/running from current if newer
            if src == "current":
                old.update(task)
                source_map[tid] = src
            else:
                for k, v in task.items():
                    old.setdefault(k, v)

    items = list(merged.values())
    items.sort(key=lambda x: (x.get("status") != "pending", x.get("priority", 50), x.get("created_at", "")))

    repaired = {
        "schema": "jarvis.action_queue.v6_4",
        "updated_at": now(),
        "guarded_by": "queue_guard_v7_7",
        "items": items,
        "integrity": {
            "current_count_before": len(current_items),
            "merged_count": len(items),
            "sources_count": len(sources),
            "backup_path": str(backup_path) if backup_path else None
        }
    }

    if args.restore:
        write_json(queue_path, repaired)

    report = {
        "schema": "jarvis.queue_guard.v7_7",
        "created_at": now(),
        "queue_path": str(queue_path),
        "backup_path": str(backup_path) if backup_path else None,
        "current_count_before": len(current_items),
        "merged_count": len(items),
        "sources_count": len(sources),
        "restored": bool(args.restore),
        "pending": len([x for x in items if x.get("status") == "pending"]),
        "completed": len([x for x in items if x.get("status") == "completed"]),
        "failed": len([x for x in items if x.get("status") == "failed"]),
        "sources_preview": sources[-10:],
    }

    write_json(out / "latest_queue_guard_report.json", report)

    md = [
        "# Jarvis Queue Guard V7.7",
        "",
        f"Created: `{report['created_at']}`",
        f"Current before: `{report['current_count_before']}`",
        f"Merged count: `{report['merged_count']}`",
        f"Pending: `{report['pending']}`",
        f"Completed: `{report['completed']}`",
        f"Failed: `{report['failed']}`",
        f"Restored: `{report['restored']}`",
        "",
        "## Backup",
        f"`{report['backup_path']}`",
    ]
    (out / "latest_queue_guard_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())