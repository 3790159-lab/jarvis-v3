from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class SecretRegistry:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.data: dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"secrets": {}}, ensure_ascii=False, indent=2), encoding="utf-8")
        self.data = json.loads(self.path.read_text(encoding="utf-8"))

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    def set_secret_ref(self, name: str, env_var: str = "", file_path: str = "", note: str = "") -> None:
        self.data.setdefault("secrets", {})
        self.data["secrets"][name] = {
            "env_var": env_var,
            "file_path": file_path,
            "note": note,
        }
        self.save()

    def resolve(self, name: str) -> dict[str, Any]:
        item = self.data.get("secrets", {}).get(name, {})
        env_var = str(item.get("env_var") or "")
        file_path = str(item.get("file_path") or "")

        value_present = False
        value_source = "missing"

        if env_var and os.getenv(env_var):
            value_present = True
            value_source = f"env:{env_var}"
        elif file_path and Path(file_path).exists():
            value_present = True
            value_source = f"file:{file_path}"

        return {
            "name": name,
            "value_present": value_present,
            "value_source": value_source,
            "note": item.get("note", ""),
        }

    def list_refs(self) -> list[dict[str, Any]]:
        return [
            {"name": k, **self.resolve(k)}
            for k in sorted(self.data.get("secrets", {}).keys())
        ]