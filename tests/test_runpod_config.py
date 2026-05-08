# -*- coding: utf-8 -*-
"""Tests for RunPod configuration loading and validation."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.block_m2_video.runpod.runpod_config import (
    RunpodConfig,
    get_runpod_config,
)


_REQUIRED_KEYS = (
    "RUNPOD_API_KEY",
    "RUNPOD_API_ENDPOINT",
    "RUNPOD_NETWORK_VOLUME_ID",
    "RUNPOD_DATACENTER",
    "RUNPOD_GPU_TYPE_ID",
    "RUNPOD_GPU_FALLBACK_ID",
    "RUNPOD_GPU_COUNT",
    "RUNPOD_TEMPLATE_ID",
    "RUNPOD_DOCKER_IMAGE",
    "COMFYUI_PORT",
    "COMFYUI_AUTH_TOKEN",
    "RUNPOD_MAX_BUDGET_USD_PER_DAY",
    "RUNPOD_MAX_POD_LIFETIME_MIN",
    "RUNPOD_GUARDIAN_CHECK_INTERVAL_SEC",
    "RUNPOD_EMERGENCY_STOP_ENABLED",
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Ensure tests do not leak through real env vars or singleton."""
    for key in _REQUIRED_KEYS:
        monkeypatch.delenv(key, raising=False)
    get_runpod_config.cache_clear()
    yield
    get_runpod_config.cache_clear()


def _write_env_file(path, mapping: dict[str, str]) -> None:
    lines = [f"{k}={v}" for k, v in mapping.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _full_env() -> dict[str, str]:
    return {
        "RUNPOD_API_KEY": "rpa_test_full_secret_key_123456",
        "RUNPOD_NETWORK_VOLUME_ID": "vol_test",
        "RUNPOD_GPU_TYPE_ID": "NVIDIA RTX TEST",
        "RUNPOD_DOCKER_IMAGE": "runpod/test:latest",
        "RUNPOD_MAX_BUDGET_USD_PER_DAY": "5.0",
        "RUNPOD_MAX_POD_LIFETIME_MIN": "30",
    }


def test_config_reads_env_runpod(tmp_path):
    env_file = tmp_path / ".env.runpod"
    _write_env_file(
        env_file,
        {
            **_full_env(),
            "RUNPOD_DATACENTER": "EU-TEST-9",
            "RUNPOD_GPU_FALLBACK_ID": "FALLBACK_GPU",
            "RUNPOD_GPU_COUNT": "2",
            "COMFYUI_PORT": "9999",
            "RUNPOD_GUARDIAN_CHECK_INTERVAL_SEC": "45",
            "RUNPOD_EMERGENCY_STOP_ENABLED": "false",
        },
    )

    cfg = RunpodConfig(_env_file=str(env_file))

    assert cfg.api_key.get_secret_value() == "rpa_test_full_secret_key_123456"
    assert cfg.network_volume_id == "vol_test"
    assert cfg.datacenter == "EU-TEST-9"
    assert cfg.gpu_type_id == "NVIDIA RTX TEST"
    assert cfg.gpu_fallback_id == "FALLBACK_GPU"
    assert cfg.gpu_count == 2
    assert cfg.docker_image == "runpod/test:latest"
    assert cfg.comfyui_port == 9999
    assert cfg.max_budget_usd_per_day == 5.0
    assert cfg.max_pod_lifetime_min == 30
    assert cfg.guardian_check_interval_sec == 45
    assert cfg.emergency_stop_enabled is False


def test_config_validates_budget_positive(tmp_path):
    env_file = tmp_path / ".env.runpod"
    _write_env_file(
        env_file,
        {**_full_env(), "RUNPOD_MAX_BUDGET_USD_PER_DAY": "-1"},
    )

    with pytest.raises(ValidationError) as info:
        RunpodConfig(_env_file=str(env_file))
    assert "max_budget_usd_per_day" in str(info.value).lower() or "budget" in str(
        info.value
    ).lower()


def test_config_validates_lifetime_positive(tmp_path):
    env_file = tmp_path / ".env.runpod"
    _write_env_file(
        env_file,
        {**_full_env(), "RUNPOD_MAX_POD_LIFETIME_MIN": "0"},
    )

    with pytest.raises(ValidationError) as info:
        RunpodConfig(_env_file=str(env_file))
    assert "lifetime" in str(info.value).lower()


def test_config_secret_str_not_in_repr(tmp_path):
    env_file = tmp_path / ".env.runpod"
    secret = "rpa_super_secret_key_should_not_appear_in_repr_xyz"
    _write_env_file(env_file, {**_full_env(), "RUNPOD_API_KEY": secret})

    cfg = RunpodConfig(_env_file=str(env_file))

    assert secret not in repr(cfg)
    assert secret not in str(cfg)
    # SecretStr default repr is "**********"
    assert "**" in repr(cfg.api_key)


def test_get_runpod_config_singleton(monkeypatch, tmp_path):
    # Point the loader at a controlled file so the test does not depend
    # on whatever happens to be in the project's real `.env.runpod`.
    env_file = tmp_path / ".env.runpod"
    _write_env_file(env_file, _full_env())

    # Re-bind RunpodConfig.__init__ to read our temp file so the cached
    # singleton picks it up on first call.
    monkeypatch.setattr(
        "app.services.block_m2_video.runpod.runpod_config.RunpodConfig",
        type(
            "PatchedRunpodConfig",
            (RunpodConfig,),
            {
                "model_config": {
                    **RunpodConfig.model_config,
                    "env_file": str(env_file),
                }
            },
        ),
    )
    get_runpod_config.cache_clear()

    a = get_runpod_config()
    b = get_runpod_config()
    assert a is b
