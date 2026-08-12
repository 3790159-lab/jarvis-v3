"""Адрес, на который садится detached-backend (:8010) — одна настройка.

Зачем отдельный модуль, а не константа в лаунчере: `scripts/run_backend_detached.py`
на импорте тянет `app.env_bootstrap` (загрузка .env), поэтому проверить его
логику в тесте, не притащив побочных эффектов, нельзя. Здесь чистая функция без
зависимостей — её и держат тесты.

Дефолт `127.0.0.1` выбран fail-closed: панель показывает переписку живых лидов,
и клон репозитория/чужая машина не должны открывать её в свою Wi-Fi по факту
запуска. Наружу выпускает только явная настройка.

Про `0.0.0.0` против tailscale-адреса: гардиан поднимает backend с загрузки
системы, Tailscale встаёт позже. Bind на 100.x в этот момент падает с
«cannot assign requested address», гардиан перезапускает процесс — краш-петля.
Поэтому слушаем все интерфейсы, а сужает доступ правило файрвола на 100.64.0.0/10.
"""
from __future__ import annotations

import ipaddress
import logging
import os
from typing import Mapping, Optional

ENV_VAR = "JARVIS_BACKEND_HOST"
DEFAULT_HOST = "127.0.0.1"

# CGNAT-диапазон, который Tailscale раздаёт узлам тайлнета.
_TAILNET = ipaddress.ip_network("100.64.0.0/10")

_log = logging.getLogger("jarvis.backend_bind")


def is_tailnet_literal(host: str) -> bool:
    """True, если `host` — конкретный адрес из tailnet-диапазона 100.64.0.0/10."""
    try:
        return ipaddress.ip_address((host or "").strip()) in _TAILNET
    except ValueError:
        return False


def resolve_bind_host(env: Optional[Mapping[str, str]] = None) -> str:
    """Хост для `uvicorn.run(...)`. Пустая/незаданная настройка — петля."""
    source = os.environ if env is None else env
    host = (source.get(ENV_VAR) or "").strip()
    if not host:
        return DEFAULT_HOST
    if is_tailnet_literal(host):
        # Не подменяем молча: тихая правка адреса прячет причину падения.
        _log.warning(
            "%s=%s — это адрес из tailnet (100.64.0.0/10). Если backend "
            "стартует раньше Tailscale, bind упадёт и гардиан уйдёт в "
            "краш-петлю. Безопаснее 0.0.0.0 + правило файрвола на 100.64.0.0/10.",
            ENV_VAR, host,
        )
    return host
