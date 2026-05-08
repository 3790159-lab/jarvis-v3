from __future__ import annotations

import json
import time
from pathlib import Path


class ExternalCallGuard:
    def __init__(self, base_dir: str | Path = "state/agent_mesh") -> None:
        self.base_dir = Path(base_dir)
        self.path = self.base_dir / "external_call_guard.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure()

    def _ensure(self) -> None:
        if not self.path.exists():
            data = {
                "services": {},
                "profiles": {
                    "claude_bridge": {"max_retries": 2, "cooldown_seconds": 180, "timeout_seconds": 120},
                    "openai": {"max_retries": 2, "cooldown_seconds": 180, "timeout_seconds": 120},
                    "ollama": {"max_retries": 1, "cooldown_seconds": 60, "timeout_seconds": 180},
                    "n8n": {"max_retries": 1, "cooldown_seconds": 120, "timeout_seconds": 60},
                }
            }
            self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self) -> dict:
        self._ensure()
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self._ensure()
            return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, data: dict) -> None:
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def profile(self, service: str) -> dict:
        data = self.load()
        return dict((data.get("profiles") or {}).get(service, {"max_retries": 1, "cooldown_seconds": 120, "timeout_seconds": 120}))

    def is_open(self, service: str) -> bool:
        data = self.load()
        row = (data.get("services") or {}).get(service, {})
        return float(row.get("open_until_ts", 0.0) or 0.0) > time.time()

    def retries(self, service: str) -> int:
        return int(self.profile(service).get("max_retries", 1) or 1)

    def timeout_seconds(self, service: str) -> int:
        return int(self.profile(service).get("timeout_seconds", 120) or 120)

    def classify_error(self, error_text: str) -> str:
        s = str(error_text or "").lower()
        if "400" in s:
            return "http_400"
        if "401" in s or "403" in s:
            return "auth_error"
        if "404" in s:
            return "http_404"
        if "429" in s:
            return "rate_limit"
        if "timed out" in s or "timeout" in s:
            return "timeout"
        if "connection" in s or "refused" in s:
            return "connection_error"
        if "json" in s:
            return "invalid_json"
        return "generic_error"

    def record_success(self, service: str) -> None:
        data = self.load()
        row = (data.setdefault("services", {})).setdefault(service, {})
        row["consecutive_failures"] = 0
        row["open_until_ts"] = 0.0
        row["last_success_ts"] = time.time()
        self.save(data)

    def record_failure(self, service: str, error_text: str) -> None:
        data = self.load()
        row = (data.setdefault("services", {})).setdefault(service, {})
        row["consecutive_failures"] = int(row.get("consecutive_failures", 0) or 0) + 1
        row["last_error"] = str(error_text or "")[:500]
        row["last_failure_ts"] = time.time()

        cooldown = int(self.profile(service).get("cooldown_seconds", 120) or 120)
        if row["consecutive_failures"] >= 3:
            row["open_until_ts"] = time.time() + cooldown

        self.save(data)

    def health(self) -> dict:
        data = self.load()
        services = []
        for service, row in sorted((data.get("services") or {}).items()):
            services.append({
                "service": service,
                "consecutive_failures": int(row.get("consecutive_failures", 0) or 0),
                "circuit_open": float(row.get("open_until_ts", 0.0) or 0.0) > time.time(),
                "open_until_ts": float(row.get("open_until_ts", 0.0) or 0.0),
                "last_error": str(row.get("last_error", "") or ""),
                "last_success_ts": float(row.get("last_success_ts", 0.0) or 0.0),
                "last_failure_ts": float(row.get("last_failure_ts", 0.0) or 0.0),
                "profile": self.profile(service),
            })
        return {"services": services, "count": len(services)}