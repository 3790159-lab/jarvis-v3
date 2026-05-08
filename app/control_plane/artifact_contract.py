from __future__ import annotations

import os
import uuid


def normalize_artifacts(artifacts, agent_id: str, task_id: str, validation_state: str = "unknown"):
    out = []
    for art in list(artifacts or []):
        metadata = dict(getattr(art, "metadata", {}) or {})
        name = str(getattr(art, "name", "") or "")
        path = str(getattr(art, "path", "") or "")
        kind = str(getattr(art, "kind", "") or "artifact")

        filename = os.path.basename(path or name or f"{task_id}_{kind}")
        _, ext = os.path.splitext(filename)

        metadata["artifact_id"] = metadata.get("artifact_id") or f"artifact_{uuid.uuid4().hex[:10]}"
        metadata["artifact_type"] = kind
        metadata["filename"] = filename
        metadata["extension"] = ext.lstrip(".")
        metadata["producer_agent"] = agent_id
        metadata["source_task_id"] = task_id
        metadata["validation_state"] = validation_state

        art.metadata = metadata
        out.append(art)
    return out