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

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import RedirectResponse

_ENV_KEY = "JARVIS_PANELS_KEY"
_COOKIE = "panels_key"

# Куда разрешено уводить после входа. БЕЛЫЙ СПИСОК, а не проверка префикса:
# открытый редирект на ручке входа — это фишинг с собственного домена, и
# «начинается с /panel/» обходится строкой `/panel/../..`.
_NEXT_ALLOWED = ("/panel/tamapi", "/panel/jarvis")
_NEXT_DEFAULT = "/panel/tamapi"


def _expected() -> str:
    return (os.getenv(_ENV_KEY) or "").strip()


def _same(provided: str, expected: str) -> bool:
    """Постоянное по времени сравнение — по БАЙТАМ, а не по строкам.

    `secrets.compare_digest` на `str` требует ASCII и бросает TypeError на
    чём угодно другом. Со строками это значило, что ключ с кириллицей давал
    500 вместо 401: отказ, отличимый от обычного, — это подсказка тому, кто
    подбирает ключ, и шум в алертах для нас."""
    return secrets.compare_digest(
        provided.strip().encode("utf-8"), expected.encode("utf-8"))


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
    if not provided or not _same(provided, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "owner key required")


COOKIE_NAME = _COOKIE

router = APIRouter(prefix="/panel", tags=["panels"])


@router.get("/login")
async def login(request: Request, key: str = "", next: str = _NEXT_DEFAULT):
    """Единственная дверь внутрь с телефона.

    Гейт принимает ключ заголовком ИЛИ cookie, но заголовок с мобильного
    браузера не отправить — до этой ручки «панель с телефона» держалась на
    расширении к браузеру. Здесь ключ приходит один раз строкой запроса и
    оседает в cookie.

    Ручка НЕ закрыта зависимостью `require_owner` намеренно: она и есть способ
    получить то, что зависимость проверяет. Её собственная проверка ниже —
    та же самая, постоянным сравнением.

    Ключ в теле ответа не повторяется: он и так уедет в историю браузера через
    query, и дублировать его в текст страницы значит раздать его ещё и
    скриншотам."""
    expected = _expected()
    if not expected:
        # Симметрия с `require_owner`: выключенная панель не имеет права
        # оставить открытой хотя бы одну дверь.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "panels disabled: JARVIS_PANELS_KEY not set")
    if not key or not _same(key, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "owner key required")

    target = next if next in _NEXT_ALLOWED else _NEXT_DEFAULT
    resp = RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)
    resp.set_cookie(
        _COOKIE, expected,
        httponly=True,          # ключ не должен доставаться скрипту на странице
        samesite="strict",      # чужой сайт не дёрнет панель от твоего имени
        path="/panel",          # ключ не поедет на остальные ручки бэкенда
        max_age=30 * 24 * 3600,
        # Secure ставим ТОЛЬКО поверх https: в тайлнете панель открывается по
        # http, и безусловный Secure сделал бы вход невозможным именно там, где
        # он и нужен. Внутри тайлнета канал шифрует сам Tailscale.
        secure=request.url.scheme == "https",
    )
    return resp
