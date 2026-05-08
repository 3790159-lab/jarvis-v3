from __future__ import annotations

from typing import Any

from .artifact_models import ArtifactTaskType
from .mission_artifact_classifier import build_plan
from .mission_artifact_models import MissionArtifactRequest
from .mission_graph_models import GraphMissionSubmitRequest, GraphStepRecord


def _infer_graph_kind(request: GraphMissionSubmitRequest) -> str:
    if request.graph_kind:
        return request.graph_kind

    text = (request.objective or "").lower()

    if "youtube" in text and ("site" in text or "landing" in text or "website" in text or "лендинг" in text or "сайт" in text):
        return "youtube_launch"

    if ("game" in text or "игра" in text) and ("site" in text or "landing" in text or "website" in text or "лендинг" in text or "сайт" in text):
        return "site_game_bundle"

    if "youtube" in text or "channel" in text or "ютуб" in text or "канал" in text:
        return "content_bundle"

    return "single_artifact"


def _youtube_constraints(base: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": base.get("name") or "AI Launch Pack",
        "niche": base.get("niche") or "AI productivity",
        "audience": base.get("audience") or "beginners and creators",
        "tone": base.get("tone") or "practical and motivating",
    }


def _table_payload(base: dict[str, Any]) -> dict[str, Any]:
    return {
        "rows": base.get("rows") or [
            {"day": 1, "title": "Channel intro", "format": "short", "status": "planned"},
            {"day": 2, "title": "Problem / solution", "format": "short", "status": "planned"},
            {"day": 3, "title": "Tool breakdown", "format": "short", "status": "planned"},
        ]
    }


def _site_constraints(base: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": base.get("title") or "Launch Landing",
        "hero": base.get("hero") or "Autonomous artifact execution",
        "subtitle": base.get("subtitle") or "Generated through a mission graph",
        "cta": base.get("cta") or "Launch",
    }


def _game_constraints(base: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": base.get("title") or "Launch Game",
    }


def build_graph_steps(request: GraphMissionSubmitRequest) -> tuple[str, list[GraphStepRecord]]:
    base_constraints = dict(request.constraints or {})
    base_payload = dict(request.payload or {})
    graph_kind = _infer_graph_kind(request)

    if graph_kind == "youtube_launch":
        steps = [
            GraphStepRecord(
                step_id="step_youtube_pack",
                title="Build YouTube Pack",
                objective="Prepare a YouTube starter pack",
                task_type=ArtifactTaskType.YOUTUBE_PACK,
                depends_on=[],
                status="ready",
                constraints=_youtube_constraints(base_constraints),
                payload=base_payload,
            ),
            GraphStepRecord(
                step_id="step_content_table",
                title="Build Content Table",
                objective="Create a content calendar table",
                task_type=ArtifactTaskType.TABLE,
                depends_on=["step_youtube_pack"],
                status="pending",
                constraints={"name": base_constraints.get("table_name") or "Launch Content Calendar"},
                payload=_table_payload(base_payload),
            ),
            GraphStepRecord(
                step_id="step_landing_site",
                title="Build Landing Site",
                objective="Create a landing page for the launch",
                task_type=ArtifactTaskType.SITE,
                depends_on=["step_youtube_pack", "step_content_table"],
                status="pending",
                constraints=_site_constraints(base_constraints),
                payload=base_payload,
            ),
        ]
        return graph_kind, steps

    if graph_kind == "site_game_bundle":
        steps = [
            GraphStepRecord(
                step_id="step_site",
                title="Build Site",
                objective="Create a landing page",
                task_type=ArtifactTaskType.SITE,
                depends_on=[],
                status="ready",
                constraints=_site_constraints(base_constraints),
                payload=base_payload,
            ),
            GraphStepRecord(
                step_id="step_game",
                title="Build Game",
                objective="Build a browser mini-game",
                task_type=ArtifactTaskType.GAME,
                depends_on=["step_site"],
                status="pending",
                constraints=_game_constraints(base_constraints),
                payload=base_payload,
            ),
        ]
        return graph_kind, steps

    if graph_kind == "content_bundle":
        steps = [
            GraphStepRecord(
                step_id="step_youtube_pack",
                title="Build YouTube Pack",
                objective="Prepare a YouTube starter pack",
                task_type=ArtifactTaskType.YOUTUBE_PACK,
                depends_on=[],
                status="ready",
                constraints=_youtube_constraints(base_constraints),
                payload=base_payload,
            ),
            GraphStepRecord(
                step_id="step_content_table",
                title="Build Content Table",
                objective="Create a content calendar table",
                task_type=ArtifactTaskType.TABLE,
                depends_on=["step_youtube_pack"],
                status="pending",
                constraints={"name": base_constraints.get("table_name") or "Content Bundle Calendar"},
                payload=_table_payload(base_payload),
            ),
        ]
        return graph_kind, steps

    # single_artifact fallback
    artifact_request = MissionArtifactRequest(
        objective=request.objective,
        constraints=base_constraints,
        payload=base_payload,
        preferred_task_type=None,
    )
    plan = build_plan(artifact_request)

    steps = [
        GraphStepRecord(
            step_id="step_single",
            title=f"Build {plan.selected_task_type.value}",
            objective=request.objective,
            task_type=plan.selected_task_type,
            depends_on=[],
            status="ready",
            constraints=base_constraints,
            payload=plan.payload,
        )
    ]
    return graph_kind, steps
