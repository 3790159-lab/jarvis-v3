# -*- coding: utf-8 -*-
"""Loader for ComfyUI workflow JSON templates.

Templates live in :mod:`app.services.block_m2_video.runpod.workflows.templates`
and are addressed by name (without the ``.json`` suffix). Callers can
inject parameter overrides via dotted paths into the workflow JSON,
e.g. ``{"6.inputs.text": "new prompt", "10.inputs.seed": 42}``.
"""
from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


def load_workflow(
    name: str,
    *,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Load a workflow JSON template by name and apply overrides.

    Args:
        name: Template name without the ``.json`` extension.
        overrides: Mapping of dotted paths into the workflow JSON to
            their replacement values. Numeric segments are treated as
            list indices, everything else as dict keys.

    Raises:
        FileNotFoundError: If no matching template exists.
        ValueError: If an override path cannot be resolved.
    """
    path = _TEMPLATES_DIR / f"{name}.json"
    if not path.is_file():
        raise FileNotFoundError(f"Workflow template not found: {path}")

    with path.open("r", encoding="utf-8") as fh:
        workflow: dict[str, Any] = json.load(fh)

    if not overrides:
        return workflow

    workflow = copy.deepcopy(workflow)
    for dotted, value in overrides.items():
        _set_dotted(workflow, dotted, value)
    logger.debug(
        "Loaded workflow '%s' with %d override(s)", name, len(overrides)
    )
    return workflow


def _set_dotted(target: Any, dotted_path: str, value: Any) -> None:
    if not dotted_path:
        raise ValueError("override path must not be empty")
    segments = dotted_path.split(".")
    cursor: Any = target
    for segment in segments[:-1]:
        cursor = _descend(cursor, segment, dotted_path)
    last = segments[-1]
    _assign(cursor, last, value, dotted_path)


def _descend(cursor: Any, segment: str, dotted_path: str) -> Any:
    if isinstance(cursor, list):
        try:
            idx = int(segment)
        except ValueError as exc:
            raise ValueError(
                f"override path {dotted_path!r}: list index expected, got {segment!r}"
            ) from exc
        try:
            return cursor[idx]
        except IndexError as exc:
            raise ValueError(
                f"override path {dotted_path!r}: index {idx} out of range"
            ) from exc
    if isinstance(cursor, dict):
        if segment not in cursor:
            raise ValueError(
                f"override path {dotted_path!r}: missing key {segment!r}"
            )
        return cursor[segment]
    raise ValueError(
        f"override path {dotted_path!r}: cannot descend into "
        f"{type(cursor).__name__} at {segment!r}"
    )


def _assign(cursor: Any, segment: str, value: Any, dotted_path: str) -> None:
    if isinstance(cursor, list):
        try:
            idx = int(segment)
        except ValueError as exc:
            raise ValueError(
                f"override path {dotted_path!r}: list index expected, got {segment!r}"
            ) from exc
        try:
            cursor[idx] = value
        except IndexError as exc:
            raise ValueError(
                f"override path {dotted_path!r}: index {idx} out of range"
            ) from exc
        return
    if isinstance(cursor, dict):
        cursor[segment] = value
        return
    raise ValueError(
        f"override path {dotted_path!r}: cannot assign to "
        f"{type(cursor).__name__}"
    )
