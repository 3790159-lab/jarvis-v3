"""Сторож к расширению allow-list: весь /panel/* закрыт ключом владельца.

Middleware в режиме ``enforce`` знает только заголовок ``X-API-Key``. Панель
живёт на другом механизме — ключ владельца в query/cookie (``panels_auth``),
поэтому телефон получал бы 401 ещё до роутера. Путь ``/panel/`` внесён в
публичный префикс middleware — и с этого момента ЕДИНСТВЕННОЕ, что защищает
переписку живых лидов, это ``require_owner`` на роутерах.

Тест держит именно это: любая ручка под /panel, кроме самой двери
``/panel/login``, обязана нести зависимость ``require_owner``. Новый роутер,
подмонтированный без неё, роняет тест, а не открывает панель миру.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.routers.panels_auth import require_owner  # noqa: E402

# Дверь входа: не закрыта require_owner намеренно — она сама сверяет ключ и
# fail-closed'ит 503, если ключа в окружении нет.
DOOR = "/panel/login"


def _panel_routers():
    from app.routers.jarvis_panel import router as jarvis_panel
    from app.routers.panels_auth import router as panels_login
    from app.routers.tamapi_dashboard import router as tamapi

    return [panels_login, tamapi, jarvis_panel]


def _dependency_calls(route) -> set:
    """Все зависимости ручки, уже с учётом router-level `dependencies=[...]`.

    FastAPI раскладывает их в `route.dependant.dependencies` (объекты
    `Dependant` с `.call`); сырой `route.dependencies` держит `Depends`,
    у которого функция лежит в `.dependency`.
    """
    calls = set()
    dependant = getattr(route, "dependant", None)
    for dep in getattr(dependant, "dependencies", []) or []:
        calls.add(getattr(dep, "call", None))
    for dep in getattr(route, "dependencies", []) or []:
        calls.add(getattr(dep, "dependency", None))
    return calls


def test_every_panel_route_is_owner_guarded():
    unguarded = []
    for router in _panel_routers():
        for route in router.routes:
            path = getattr(route, "path", "")
            if not path.startswith("/panel"):
                continue
            if path == DOOR:
                continue
            if require_owner not in _dependency_calls(route):
                unguarded.append(f"{sorted(getattr(route, 'methods', []) or [])} {path}")
    assert not unguarded, (
        "ручки под /panel без require_owner — публичный префикс сделает их "
        f"открытыми миру: {unguarded}"
    )


def test_panels_disabled_without_key(monkeypatch):
    """Выключенная панель не монтирует даже дверь — публичный префикс
    ведёт в пустоту, а не в открытую панель."""
    import app.routers.panels_auth as pa

    monkeypatch.delenv(pa._ENV_KEY, raising=False)
    assert pa.panels_enabled() is False

    monkeypatch.setenv(pa._ENV_KEY, "x" * 64)
    assert pa.panels_enabled() is True


@pytest.mark.asyncio
async def test_require_owner_rejects_wrong_key(monkeypatch):
    """Ключ владельца остаётся единственной защитой после расширения allow-list."""
    from fastapi import HTTPException

    import app.routers.panels_auth as pa

    monkeypatch.setenv(pa._ENV_KEY, "correct-key")

    class _Req:
        cookies: dict = {}

    with pytest.raises(HTTPException) as ei:
        await pa.require_owner(_Req(), x_panels_key="wrong-key")
    assert ei.value.status_code == 401

    # Правильный ключ проходит молча.
    await pa.require_owner(_Req(), x_panels_key="correct-key")


def test_panel_routers_actually_mounted_under_panel_prefix():
    """Защита от переименования префикса: сторож обязан кого-то охранять."""
    paths = [
        getattr(r, "path", "")
        for router in _panel_routers()
        for r in router.routes
    ]
    panel_paths = [p for p in paths if p.startswith("/panel")]
    assert len(panel_paths) >= 3, f"ожидали ручки под /panel, нашли: {paths}"
    assert DOOR in panel_paths
