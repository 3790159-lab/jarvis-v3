from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .artifact_models import ArtifactFileRecord, ArtifactManifest
from .sandbox_policy import ensure_directory, resolve_under


class ArtifactRegistry:
    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.artifacts_root = resolve_under(project_root, "artifacts")
        self.runtime_root = resolve_under(self.artifacts_root, "runtime_outputs")
        self.registry_root = resolve_under(self.artifacts_root, "registry")
        ensure_directory(self.runtime_root)
        ensure_directory(self.registry_root)

    def make_output_dir(self, category: str, folder_name: str) -> Path:
        target = resolve_under(self.runtime_root, category, folder_name)
        ensure_directory(target)
        return target

    def build_file_records(self, output_dir: Path, created_files: Iterable[Path]) -> list[ArtifactFileRecord]:
        records: list[ArtifactFileRecord] = []
        for path in created_files:
            if not path.exists() or not path.is_file():
                continue
            rel = path.relative_to(output_dir).as_posix()
            records.append(
                ArtifactFileRecord(
                    relative_path=rel,
                    size_bytes=path.stat().st_size,
                )
            )
        return records

    def write_manifest(self, manifest: ArtifactManifest, output_dir: Path) -> Path:
        manifest_path = resolve_under(output_dir, "manifest.json")
        manifest_path.write_text(
            json.dumps(manifest.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        registry_copy = resolve_under(self.registry_root, f"{manifest.task_id}.json")
        registry_copy.write_text(
            json.dumps(manifest.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return manifest_path
