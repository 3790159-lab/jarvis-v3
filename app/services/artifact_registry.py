from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


BASE_DIR = Path("jarvis_stage3_artifacts") / "artifact_runtime"
MISSIONS_DIR = BASE_DIR / "missions"

MISSIONS_DIR.mkdir(parents=True, exist_ok=True)


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    return str(value)


def _mission_dir(mission_id: str) -> Path:
    path = MISSIONS_DIR / mission_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _manifest_path(mission_id: str) -> Path:
    return _mission_dir(mission_id) / "artifact_manifest.json"


def _result_path(mission_id: str) -> Path:
    return _mission_dir(mission_id) / "mission_result.json"


def _read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_json_safe(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _artifact_record_for_path(path_value: str, source: str, role: str) -> Optional[Dict[str, Any]]:
    try:
        path = Path(path_value)
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        else:
            path = path.resolve()
    except Exception:
        return None

    exists = path.exists()
    record = {
        "path": str(path),
        "name": path.name,
        "extension": path.suffix.lower(),
        "exists": exists,
        "source": source,
        "role": role,
    }

    if exists and path.is_file():
        try:
            record["size_bytes"] = path.stat().st_size
        except Exception:
            record["size_bytes"] = None

    return record


def _extract_artifacts_from_result(result: Dict[str, Any], source: str, role: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    if not isinstance(result, dict):
        return items

    output = result.get("output")
    if isinstance(output, dict):
        path_value = output.get("path")
        if isinstance(path_value, str) and path_value.strip():
            record = _artifact_record_for_path(path_value, source=source, role=role)
            if record:
                items.append(record)

    return items


def _dedupe_artifacts(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out: List[Dict[str, Any]] = []
    for item in items:
        key = item.get("path")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def collect_mission_artifacts(mission_id: str, steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    artifacts: List[Dict[str, Any]] = []

    for step in steps or []:
        if not isinstance(step, dict):
            continue
        metadata = step.get("metadata") or {}

        tool_result = metadata.get("tool_result")
        if isinstance(tool_result, dict):
            artifacts.extend(
                _extract_artifacts_from_result(
                    tool_result,
                    source=f"step:{step.get('step_id')}",
                    role="final_step_result",
                )
            )

        chain_result = metadata.get("tool_chain_result")
        if isinstance(chain_result, dict):
            final_result = chain_result.get("final_result")
            if isinstance(final_result, dict):
                artifacts.extend(
                    _extract_artifacts_from_result(
                        final_result,
                        source=f"step:{step.get('step_id')}:chain_final",
                        role="chain_final_result",
                    )
                )

        execution_attempts = metadata.get("execution_attempts") or []
        if isinstance(execution_attempts, list):
            for idx, attempt in enumerate(execution_attempts, start=1):
                if not isinstance(attempt, dict):
                    continue
                result = attempt.get("result")
                if isinstance(result, dict):
                    artifacts.extend(
                        _extract_artifacts_from_result(
                            result,
                            source=f"step:{step.get('step_id')}:attempt:{idx}",
                            role="intermediate_result",
                        )
                    )

    artifacts = _dedupe_artifacts(artifacts)

    manifest = {
        "mission_id": mission_id,
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
    }
    _write_json(_manifest_path(mission_id), manifest)
    return manifest


def save_mission_result(mission_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    safe_payload = _json_safe(payload)
    _write_json(_result_path(mission_id), safe_payload)
    return safe_payload


def get_artifact_manifest(mission_id: str) -> Optional[Dict[str, Any]]:
    return _read_json(_manifest_path(mission_id), None)


def get_mission_result(mission_id: str) -> Optional[Dict[str, Any]]:
    return _read_json(_result_path(mission_id), None)


def list_mission_ids() -> List[str]:
    if not MISSIONS_DIR.exists():
        return []
    out = []
    for path in sorted(MISSIONS_DIR.iterdir()):
        if path.is_dir():
            out.append(path.name)
    return out


def list_mission_summaries() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for mission_id in list_mission_ids():
        manifest = get_artifact_manifest(mission_id) or {}
        result = get_mission_result(mission_id) or {}
        items.append(
            {
                "mission_id": mission_id,
                "artifact_count": int(manifest.get("artifact_count") or 0),
                "ok": result.get("ok"),
                "error": result.get("error"),
                "final_summary": result.get("final_summary"),
            }
        )
    return items