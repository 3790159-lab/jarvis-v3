"""Адрес, на который садится detached-backend, — НАСТРОЙКА, а не хардкод.

Почему не bind на tailscale-адрес: backend стартует гардианом с загрузки
системы, Tailscale поднимается позже. Bind на 100.x упал бы с
«cannot assign requested address», гардиан перезапустил бы процесс — и так
по кругу, краш-петля. Поэтому здесь только два разумных значения:
``127.0.0.1`` (дефолт, fail-closed) и ``0.0.0.0``; литерал из tailnet-диапазона
распознаётся отдельно, чтобы предупредить, а не молча уронить.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.backend_bind import (  # noqa: E402
    DEFAULT_HOST,
    ENV_VAR,
    is_tailnet_literal,
    resolve_bind_host,
)


# ── дефолт fail-closed ──────────────────────────────────────────────────────

def test_default_is_loopback_when_unset():
    """Без настройки — петля. Клон репозитория не открывает панель наружу."""
    assert DEFAULT_HOST == "127.0.0.1"
    assert resolve_bind_host({}) == "127.0.0.1"


def test_blank_value_falls_back_to_default():
    assert resolve_bind_host({ENV_VAR: ""}) == "127.0.0.1"
    assert resolve_bind_host({ENV_VAR: "   "}) == "127.0.0.1"


# ── настройка ───────────────────────────────────────────────────────────────

def test_env_var_overrides():
    assert resolve_bind_host({ENV_VAR: "0.0.0.0"}) == "0.0.0.0"


def test_whitespace_is_trimmed():
    assert resolve_bind_host({ENV_VAR: "  0.0.0.0\t"}) == "0.0.0.0"


def test_reads_os_environ_when_no_mapping_given(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "0.0.0.0")
    assert resolve_bind_host() == "0.0.0.0"
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert resolve_bind_host() == "127.0.0.1"


# ── распознавание tailnet-литерала (грабли краш-петли) ──────────────────────

@pytest.mark.parametrize(
    "host, expected",
    [
        ("100.64.0.0", True),        # нижняя граница 100.64.0.0/10
        ("100.102.179.47", True),    # этот десктоп в тайлнете
        ("100.127.255.255", True),   # верхняя граница
        ("100.63.255.255", False),   # на единицу ниже диапазона
        ("100.128.0.0", False),      # на единицу выше диапазона
        ("0.0.0.0", False),
        ("127.0.0.1", False),
        ("192.168.1.10", False),
        ("не-адрес", False),
        ("", False),
    ],
)
def test_is_tailnet_literal(host, expected):
    assert is_tailnet_literal(host) is expected


def test_tailnet_bind_is_allowed_but_warned(caplog):
    """Не запрещаем — предупреждаем. Молчаливая подмена адреса хуже явного лога."""
    with caplog.at_level("WARNING"):
        assert resolve_bind_host({ENV_VAR: "100.102.179.47"}) == "100.102.179.47"
    assert any("tailnet" in r.message.lower() for r in caplog.records)


# ── сторож: в скрипте не должно остаться хардкода ───────────────────────────

def test_launcher_has_no_hardcoded_host():
    src = (ROOT / "scripts" / "run_backend_detached.py").read_text(encoding="utf-8")
    assert "resolve_bind_host" in src, "лаунчер обязан брать host из настройки"
    assert not re.search(r'HOST\s*=\s*["\']127\.0\.0\.1["\']', src)
    assert not re.search(r'host\s*=\s*["\']\d', src), "host передаётся литералом"
