from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .mission_graph_models import GraphMissionRecord


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class MissionGraphPackager:
    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()
        self.bundles_root = self.project_root / "artifacts" / "mission_graph" / "bundles"
        self.bundles_root.mkdir(parents=True, exist_ok=True)

    def package(self, mission: GraphMissionRecord) -> dict[str, Any]:
        bundle_dir = self.bundles_root / mission.graph_mission_id
        bundle_dir.mkdir(parents=True, exist_ok=True)

        steps_payload = []
        outputs_count = 0

        for step in mission.steps:
            output = dict(step.output or {})
            if output:
                outputs_count += 1

            steps_payload.append(
                {
                    "step_id": step.step_id,
                    "title": step.title,
                    "task_type": step.task_type.value,
                    "status": step.status,
                    "attempts": step.attempts,
                    "depends_on": list(step.depends_on),
                    "constraints": dict(step.constraints or {}),
                    "payload": dict(step.payload or {}),
                    "output": output,
                }
            )

        manifest = {
            "packaged_at": _now_iso(),
            "graph_mission_id": mission.graph_mission_id,
            "graph_kind": mission.graph_kind,
            "objective": mission.objective,
            "status": mission.status,
            "execution_state": mission.execution_state,
            "summary": dict(mission.summary or {}),
            "steps_total": len(mission.steps),
            "outputs_count": outputs_count,
            "mission_path": str(self.project_root / "artifacts" / "mission_graph" / "missions" / f"{mission.graph_mission_id}.json"),
            "steps": steps_payload,
        }

        _write_json(bundle_dir / "bundle_manifest.json", manifest)
        _write_json(bundle_dir / "step_outputs.json", {"steps": steps_payload})

        summary_md = [
            f"# Mission Bundle: {mission.graph_mission_id}",
            "",
            f"Objective: {mission.objective}",
            f"Graph kind: {mission.graph_kind}",
            f"Status: {mission.status}",
            f"Execution state: {mission.execution_state}",
            "",
            "## Step Summary",
            "",
        ]

        for step in mission.steps:
            summary_md.append(f"- {step.step_id} | {step.task_type.value} | {step.status} | attempts={step.attempts}")

        summary_md.append("")
        summary_md.append("## Outputs")
        summary_md.append("")

        for step in mission.steps:
            output = dict(step.output or {})
            if not output:
                continue
            summary_md.append(f"### {step.step_id}")
            for k, v in output.items():
                summary_md.append(f"- {k}: {v}")
            summary_md.append("")

        _write_text(bundle_dir / "bundle_summary.md", "\n".join(summary_md) + "\n")

        return {
            "ok": True,
            "graph_mission_id": mission.graph_mission_id,
            "bundle_dir": str(bundle_dir),
            "bundle_manifest_path": str(bundle_dir / "bundle_manifest.json"),
            "bundle_summary_path": str(bundle_dir / "bundle_summary.md"),
            "step_outputs_path": str(bundle_dir / "step_outputs.json"),
            "outputs_count": outputs_count,
        }
