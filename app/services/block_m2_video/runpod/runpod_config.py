# -*- coding: utf-8 -*-
"""RunPod configuration loaded from `.env` and `.env.runpod`.

`.env.runpod` takes precedence over `.env` for any overlapping keys, so
secrets specific to the RunPod integration can live outside the main
application env file.

Use :func:`get_runpod_config` for cached singleton access. The settings are
NOT instantiated at module import time so tests can monkey-patch the
environment or call ``get_runpod_config.cache_clear()`` to force a reload.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent

# Upper bound for GPUs per pod. RunPod tops out at 8 GPUs per pod; capping
# here guards against a typo silently provisioning a runaway multi-GPU bill.
_MAX_GPU_COUNT = 8


class RunpodConfig(BaseSettings):
    """Settings for the RunPod / ComfyUI Video Factory."""

    # Order matters: later files override earlier ones, so `.env.runpod`
    # wins over the shared `.env`.
    model_config = SettingsConfigDict(
        env_file=(str(_PROJECT_ROOT / ".env"), str(_PROJECT_ROOT / ".env.runpod")),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    api_key: SecretStr = Field(alias="RUNPOD_API_KEY")
    api_endpoint: str = Field(
        default="https://api.runpod.io/graphql", alias="RUNPOD_API_ENDPOINT"
    )

    network_volume_id: str = Field(alias="RUNPOD_NETWORK_VOLUME_ID")
    datacenter: str = Field(default="EU-RO-1", alias="RUNPOD_DATACENTER")

    gpu_type_id: str = Field(alias="RUNPOD_GPU_TYPE_ID")
    gpu_fallback_id: str | None = Field(default=None, alias="RUNPOD_GPU_FALLBACK_ID")
    gpu_count: int = Field(default=1, alias="RUNPOD_GPU_COUNT")

    template_id: str | None = Field(default=None, alias="RUNPOD_TEMPLATE_ID")
    docker_image: str = Field(alias="RUNPOD_DOCKER_IMAGE")

    comfyui_port: int = Field(default=8188, alias="COMFYUI_PORT")
    comfyui_auth_token: SecretStr | None = Field(
        default=None, alias="COMFYUI_AUTH_TOKEN"
    )

    max_budget_usd_per_day: float = Field(alias="RUNPOD_MAX_BUDGET_USD_PER_DAY")
    max_pod_lifetime_min: int = Field(alias="RUNPOD_MAX_POD_LIFETIME_MIN")
    guardian_check_interval_sec: int = Field(
        default=30, alias="RUNPOD_GUARDIAN_CHECK_INTERVAL_SEC"
    )
    emergency_stop_enabled: bool = Field(
        default=True, alias="RUNPOD_EMERGENCY_STOP_ENABLED"
    )

    @field_validator("max_budget_usd_per_day")
    @classmethod
    def _validate_budget_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("RUNPOD_MAX_BUDGET_USD_PER_DAY must be > 0")
        return value

    @field_validator("max_pod_lifetime_min")
    @classmethod
    def _validate_lifetime_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("RUNPOD_MAX_POD_LIFETIME_MIN must be > 0")
        return value

    @field_validator("gpu_count")
    @classmethod
    def _validate_gpu_count(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("RUNPOD_GPU_COUNT must be > 0")
        if value > _MAX_GPU_COUNT:
            raise ValueError(
                f"RUNPOD_GPU_COUNT must be <= {_MAX_GPU_COUNT} (got {value}); "
                "cap guards against a runaway multi-GPU bill"
            )
        return value

    @field_validator("comfyui_port")
    @classmethod
    def _validate_port(cls, value: int) -> int:
        if not (1 <= value <= 65535):
            raise ValueError("COMFYUI_PORT must be in [1, 65535]")
        return value

    @model_validator(mode="after")
    def _validate_security_and_completeness(self) -> "RunpodConfig":
        """Cross-field validation (P36): fail loudly on misconfigurations that
        would otherwise silently produce a broken or insecure deployment.

        Runs eagerly at instantiation, so calling ``get_runpod_config()`` once
        at application startup surfaces these problems before any pod work
        begins rather than lazily mid-flight.
        """
        # template_id=None would deploy a bare Docker image with NO bootstrap
        # (no model weights, no patches) — a silent failure. Make it explicit.
        if self.template_id is None:
            raise ValueError(
                "RUNPOD_TEMPLATE_ID is not set: deploying without a template "
                "launches a bare image with no bootstrap (no weights/patches). "
                "Set RUNPOD_TEMPLATE_ID explicitly."
            )

        # An empty/missing ComfyUI auth token exposes ComfyUI WITHOUT
        # authentication behind the public proxy — a real security hole.
        token = (
            self.comfyui_auth_token.get_secret_value()
            if self.comfyui_auth_token is not None
            else ""
        )
        if not token.strip():
            logger.warning(
                "SECURITY: COMFYUI_AUTH_TOKEN is empty — ComfyUI will be "
                "exposed WITHOUT authentication on the public proxy. Set "
                "COMFYUI_AUTH_TOKEN to protect the endpoint."
            )

        # The RunPod API key is read from a plaintext .env file. Remind
        # operators to rotate it and keep .env out of version control.
        logger.warning(
            "NOTE: RUNPOD_API_KEY is loaded from plaintext .env — rotate it "
            "periodically and ensure .env files are gitignored."
        )
        return self


@lru_cache(maxsize=1)
def get_runpod_config() -> RunpodConfig:
    """Return a process-wide singleton :class:`RunpodConfig`.

    Tests can call ``get_runpod_config.cache_clear()`` to force a reload
    after mutating the environment.
    """
    cfg = RunpodConfig()
    logger.debug(
        "RunpodConfig loaded (datacenter=%s, gpu_type_id=%s, max_budget=%.2f)",
        cfg.datacenter,
        cfg.gpu_type_id,
        cfg.max_budget_usd_per_day,
    )
    return cfg
