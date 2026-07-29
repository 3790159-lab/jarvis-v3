"""Owner-only гейт для обеих панелей.

Панели показывают переписку живых лидов (клиентский дашборд) и PID/ветки/сроки
ключей (панель Джарвиса). Это НЕ те данные, которые можно отдать «кому попало с
ключом от API», поэтому здесь отдельный уровень поверх общего
`auth_guard_middleware`: ключ обязан быть валидным И принадлежать владельцу.

РЕШЕНИЕ: гейт — явная FastAPI-зависимость на каждом роутере, а не доверие
middleware. Причина: middleware сегодня по умолчанию `off`
(JARVIS_AUTH_MIDDLEWARE_MODE), и панель, полагающаяся только на него, при
дефолтном конфиге открылась бы целиком. Зависимость закрыта по умолчанию и
открывается только явно заданным ключом.
"""
from __future__ import annotations

import os
import secrets

from fastapi import Header, HTTPException, Request, status

_ENV_KEY = "JARVIS_PANELS_KEY"
_COOKIE = "panels_key"


def _expected() -> str:
    return (os.getenv(_ENV_KEY) or "").strip()


def panels_enabled() -> bool:
    """Панели существуют, только если ключ задан. Без ключа роутеры не
    монтируются вовсе — «выключено» надёжнее, чем «включено, но защищено»."""
    return bool(_expected())


async def require_owner(
    request: Request,
    x_panels_key: str | None = Header(default=None, alias="X-Panels-Key"),
) -> None:
    """Ключ из заголовка или cookie. Cookie — чтобы браузер не требовал
    расширения для каждой картинки/фетча; она HttpOnly+SameSite=Strict и
    ставится единственной ручкой входа."""
    expected = _expected()
    if not expected:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "panels disabled: JARVIS_PANELS_KEY not set")
    provided = x_panels_key or request.cookies.get(_COOKIE) or ""
    if not provided or not secrets.compare_digest(provided.strip(), expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "owner key required")


COOKIE_NAME = _COOKIE
