from __future__ import annotations

import json
import time
from enum import Enum
from pathlib import Path

from pydantic import BaseModel


class ProviderStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class ProviderRecord(BaseModel):
    provider: str
    status: ProviderStatus = ProviderStatus.HEALTHY
    success_count: int = 0
    failure_count: int = 0
    consecutive_failures: int = 0
    last_error: str = ""
    last_success_ts: float = 0.0
    last_failure_ts: float = 0.0
    latency_ms_avg: float = 0.0


class ProviderHealthRegistry:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.providers: dict[str, ProviderRecord] = {}
        self.load()
        self.ensure_defaults()

    def ensure_defaults(self) -> None:
        changed = False
        for name in ["cloud", "openai_compatible", "ollama"]:
            if name not in self.providers:
                self.providers[name] = ProviderRecord(provider=name)
                changed = True
        if changed:
            self.save()

    def load(self) -> None:
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"providers": []}, ensure_ascii=False, indent=2), encoding="utf-8")

        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.providers = {}
        for item in raw.get("providers", []):
            rec = ProviderRecord.model_validate(item)
            self.providers[rec.provider] = rec

    def save(self) -> None:
        data = {"providers": [p.model_dump(mode="json") for p in self.providers.values()]}
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def record_success(self, provider: str, latency_ms: float = 0.0) -> None:
        rec = self.providers.setdefault(provider, ProviderRecord(provider=provider))
        rec.success_count += 1
        rec.consecutive_failures = 0
        rec.last_success_ts = time.time()
        rec.status = ProviderStatus.HEALTHY

        if latency_ms > 0:
            if rec.latency_ms_avg <= 0:
                rec.latency_ms_avg = latency_ms
            else:
                rec.latency_ms_avg = round((rec.latency_ms_avg * 0.7) + (latency_ms * 0.3), 2)

        self.save()

    def record_failure(self, provider: str, error: str = "") -> None:
        rec = self.providers.setdefault(provider, ProviderRecord(provider=provider))
        rec.failure_count += 1
        rec.consecutive_failures += 1
        rec.last_error = error or ""
        rec.last_failure_ts = time.time()

        if rec.consecutive_failures >= 3:
            rec.status = ProviderStatus.UNAVAILABLE
        elif rec.consecutive_failures >= 1:
            rec.status = ProviderStatus.DEGRADED

        self.save()

    def mark_healthy(self, provider: str) -> None:
        rec = self.providers.setdefault(provider, ProviderRecord(provider=provider))
        rec.status = ProviderStatus.HEALTHY
        rec.consecutive_failures = 0
        self.save()

    def as_router_context(self) -> dict[str, str]:
        return {name: rec.status.value for name, rec in self.providers.items()}

    def summary(self) -> dict:
        return {
            "providers": [p.model_dump(mode="json") for p in self.providers.values()]
        }