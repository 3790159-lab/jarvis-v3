from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class StrategyRuntimeStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.data: dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.data = {
                "active_variant_id": "balanced_cloud_primary",
                "active_variant_name": "Balanced Cloud Primary",
                "knobs": {
                    "consultation_bonus": 0,
                    "qa_strictness": "medium",
                    "encourage_memory_support": True,
                    "prefer_claude_codegen": True,
                    "prefer_openai_planning": True,
                    "integration_mode": "guarded_native",
                },
                "updated_ts": time.time(),
            }
            self.save()
            return
        self.data = json.loads(self.path.read_text(encoding="utf-8"))

    def save(self) -> None:
        self.data["updated_ts"] = time.time()
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    def activate(self, variant_id: str, variant_name: str, knobs: dict[str, Any]) -> None:
        self.data = {
            "active_variant_id": variant_id,
            "active_variant_name": variant_name,
            "knobs": dict(knobs),
            "updated_ts": time.time(),
        }
        self.save()

    def get_knob(self, name: str, default=None):
        self.load()
        return self.data.get("knobs", {}).get(name, default)

    def dump(self) -> dict[str, Any]:
        self.load()
        return dict(self.data)