from __future__ import annotations

import json
import os
import socket
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import URLError, HTTPError
from urllib.request import urlopen


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class ExternalCapability:
    name: str
    category: str
    enabled: bool
    mode: str
    safe_actions: List[str] = field(default_factory=list)
    blocked_actions: List[str] = field(default_factory=list)
    endpoints: List[str] = field(default_factory=list)
    required_env_vars: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


@dataclass
class ReadinessCheck:
    name: str
    ok: bool
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SystemReadinessProfile:
    system_name: str
    category: str
    mode: str
    ready: bool
    checks: List[ReadinessCheck] = field(default_factory=list)
    safe_actions: List[str] = field(default_factory=list)
    blocked_actions: List[str] = field(default_factory=list)
    next_best_action: str = ""
    assessed_at: str = field(default_factory=utc_now_iso)


class JarvisExternalSystemsReadiness:
    """
    Block 5:
    - define external capability registry
    - assess readiness for external systems
    - enforce safe/blocked action boundaries
    - provide execution modes for future integrations
    """

    def __init__(
        self,
        project_root: str | Path,
        artifacts_root: Optional[str | Path] = None,
        backend_base_url: str = "http://127.0.0.1:8015",
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.backend_base_url = backend_base_url.rstrip("/")
        self.artifacts_root = (
            Path(artifacts_root).resolve()
            if artifacts_root
            else self.project_root / "jarvis_stage3_artifacts" / "external_systems_readiness"
        )

        self.registry_dir = ensure_dir(self.artifacts_root / "registry")
        self.profiles_dir = ensure_dir(self.artifacts_root / "profiles")
        self.logs_dir = ensure_dir(self.artifacts_root / "logs")
        self.runtime_dir = ensure_dir(self.artifacts_root / "runtime")

    # ------------------------------------------------------------------
    # persistence / logging
    # ------------------------------------------------------------------
    def _write_json(self, path: Path, payload: Any) -> None:
        ensure_dir(path.parent)
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def _log(self, level: str, message: str) -> None:
        line = f"[{utc_now_iso()}] [{level.upper()}] {message}"
        print(line)
        with (self.logs_dir / "external_systems_readiness.log").open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    # ------------------------------------------------------------------
    # registry
    # ------------------------------------------------------------------
    def build_default_registry(self) -> List[ExternalCapability]:
        registry = [
            ExternalCapability(
                name="telegram_bridge",
                category="messaging",
                enabled=True,
                mode="safe_observe_and_send",
                safe_actions=["health_check", "send_safe_message", "read_bot_config"],
                blocked_actions=["mass_broadcast", "unsafe_command_execution"],
                required_env_vars=["TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_CHAT_ID"],
                notes=["Use only controlled outbound messages and health checks at this stage."],
            ),
            ExternalCapability(
                name="n8n_local",
                category="automation",
                enabled=True,
                mode="safe_observe_and_template_prepare",
                safe_actions=["health_check", "list_templates", "prepare_safe_workflow_payload"],
                blocked_actions=["unsafe_deploy", "unbounded_workflow_execution"],
                endpoints=["http://127.0.0.1:5678/"],
                notes=["Prefer local health and template readiness before real deployment."],
            ),
            ExternalCapability(
                name="google_workspace",
                category="workspace",
                enabled=True,
                mode="safe_readiness_only",
                safe_actions=["health_check", "credential_presence_check", "scopes_check"],
                blocked_actions=["unsafe_bulk_write", "unvalidated_calendar_mass_actions"],
                endpoints=[
                    f"{self.backend_base_url}/api/spreadsheets/health",
                    f"{self.backend_base_url}/health",
                ],
                required_env_vars=[
                    "GOOGLE_SERVICE_ACCOUNT_JSON",
                    "GOOGLE_OAUTH_CLIENT_SECRET_JSON",
                    "GOOGLE_OAUTH_TOKEN_JSON",
                ],
                notes=["Focus on auth/config readiness first, then narrow write scopes."],
            ),
            ExternalCapability(
                name="local_http_tools",
                category="http",
                enabled=True,
                mode="safe_local_http",
                safe_actions=["health_check", "local_get", "local_safe_post"],
                blocked_actions=["arbitrary_external_post", "unsafe_remote_calls"],
                endpoints=[
                    f"{self.backend_base_url}/health",
                    f"{self.backend_base_url}/api/ai/health",
                    f"{self.backend_base_url}/api/autonomy/health",
                ],
                notes=["Restrict to localhost and approved endpoints only."],
            ),
            ExternalCapability(
                name="filesystem_adapters",
                category="filesystem",
                enabled=True,
                mode="allowlisted_fs",
                safe_actions=["read_allowlisted_file", "write_allowlisted_file", "snapshot_file"],
                blocked_actions=["write_secret_files", "write_system_dirs", "delete_unbounded_trees"],
                notes=["Allow only project-root and pre-approved artifact zones."],
            ),
        ]

        for item in registry:
            self._write_json(self.registry_dir / f"{item.name}.json", asdict(item))

        return registry

    # ------------------------------------------------------------------
    # checks
    # ------------------------------------------------------------------
    def _env_var_present(self, key: str) -> ReadinessCheck:
        value = os.environ.get(key)
        return ReadinessCheck(
            name=f"env:{key}",
            ok=bool(value),
            details={"present": bool(value)},
        )

    def _http_check(self, url: str, timeout: int = 10) -> ReadinessCheck:
        try:
            with urlopen(url, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return ReadinessCheck(
                    name=f"http:{url}",
                    ok=200 <= resp.status < 300,
                    details={"status": resp.status, "body_excerpt": body[:400]},
                )
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            return ReadinessCheck(
                name=f"http:{url}",
                ok=False,
                details={"error": str(exc)},
            )

    def _tcp_port_check(self, host: str, port: int, timeout: float = 1.5) -> ReadinessCheck:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect((host, port))
            return ReadinessCheck(
                name=f"tcp:{host}:{port}",
                ok=True,
                details={"reachable": True},
            )
        except OSError as exc:
            return ReadinessCheck(
                name=f"tcp:{host}:{port}",
                ok=False,
                details={"reachable": False, "error": str(exc)},
            )
        finally:
            sock.close()

    # ------------------------------------------------------------------
    # profile assessment
    # ------------------------------------------------------------------
    def assess_capability(self, capability: ExternalCapability) -> SystemReadinessProfile:
        checks: List[ReadinessCheck] = []

        for env_key in capability.required_env_vars:
            checks.append(self._env_var_present(env_key))

        for endpoint in capability.endpoints:
            checks.append(self._http_check(endpoint))

        if capability.name == "n8n_local":
            checks.append(self._tcp_port_check("127.0.0.1", 5678))

        if capability.name == "telegram_bridge":
            checks.append(self._http_check(f"{self.backend_base_url}/health"))

        if capability.name == "filesystem_adapters":
            checks.append(
                ReadinessCheck(
                    name="project_root_exists",
                    ok=self.project_root.exists(),
                    details={"path": str(self.project_root)},
                )
            )

        ready = all(ch.ok for ch in checks) if checks else capability.enabled

        if ready:
            next_best_action = f"{capability.name} is ready for controlled safe actions."
        else:
            missing = [ch.name for ch in checks if not ch.ok][:5]
            next_best_action = (
                f"Stabilize {capability.name} by resolving failed checks: {', '.join(missing)}"
                if missing
                else f"Collect more readiness evidence for {capability.name}"
            )

        profile = SystemReadinessProfile(
            system_name=capability.name,
            category=capability.category,
            mode=capability.mode,
            ready=ready,
            checks=checks,
            safe_actions=capability.safe_actions,
            blocked_actions=capability.blocked_actions,
            next_best_action=next_best_action,
        )
        self._write_json(self.profiles_dir / f"{capability.name}.json", self._profile_payload(profile))
        self._log("info", f"assess_capability name={capability.name} ready={profile.ready} mode={profile.mode}")
        return profile

    def assess_all(self) -> List[SystemReadinessProfile]:
        registry = self.build_default_registry()
        profiles = [self.assess_capability(item) for item in registry]
        return profiles

    def _profile_payload(self, profile: SystemReadinessProfile) -> Dict[str, Any]:
        return {
            "system_name": profile.system_name,
            "category": profile.category,
            "mode": profile.mode,
            "ready": profile.ready,
            "checks": [asdict(c) for c in profile.checks],
            "safe_actions": profile.safe_actions,
            "blocked_actions": profile.blocked_actions,
            "next_best_action": profile.next_best_action,
            "assessed_at": profile.assessed_at,
        }

    def collect_metrics(self) -> Dict[str, Any]:
        profile_files = list(self.profiles_dir.glob("*.json"))
        ready_count = 0
        not_ready_count = 0
        modes: Dict[str, int] = {}

        for path in profile_files:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("ready"):
                    ready_count += 1
                else:
                    not_ready_count += 1
                mode = data.get("mode", "unknown")
                modes[mode] = modes.get(mode, 0) + 1
            except Exception:
                pass

        metrics = {
            "profiles_count": len(profile_files),
            "ready_count": ready_count,
            "not_ready_count": not_ready_count,
            "modes": modes,
            "collected_at": utc_now_iso(),
            "artifacts_root": str(self.artifacts_root),
        }
        self._write_json(self.runtime_dir / "metrics_summary.json", metrics)
        return metrics