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

# Сверяем зависимость ПО ИМЕНИ, а не по объекту. `tests/chatter/test_panels_web.py`
# делает `importlib.reload` панельных модулей: после него `require_owner` — новый
# объект, а роутеры держат прежний. Сверка по identity давала на полном прогоне
# ложный красный «все ручки открыты» при живой защите — то есть сторож врал ровно
# в том прогоне, ради которого он написан.
OWNER_DEP = ("app.routers.panels_auth", "require_owner")

# Двери: не закрыты require_owner намеренно. `/panel/login` сама сверяет ключ и
# fail-closed'ит 503 без ключа в окружении. `/panel` — указатель: он ничего не
# показывает, только разводит владельца на панель, а чужого на форму; закрой его
# зависимостью — и он вместо формы отдаст 401, то есть перестанет быть указателем.
DOORS = frozenset({"/panel/login", "/panel"})


def _panel_routers():
    from app.routers.jarvis_panel import router as jarvis_panel
    from app.routers.panels_auth import router as panels_login
    from app.routers.tamapi_dashboard import router as tamapi

    return [panels_login, tamapi, jarvis_panel]


def _name_of(fn) -> tuple:
    return (getattr(fn, "__module__", ""), getattr(fn, "__qualname__", ""))


def _dependency_names(route) -> set:
    """Имена всех зависимостей ручки, уже с учётом router-level `dependencies=[...]`.

    FastAPI раскладывает их в `route.dependant.dependencies` (объекты
    `Dependant` с `.call`); сырой `route.dependencies` держит `Depends`,
    у которого функция лежит в `.dependency`.
    """
    names = set()
    dependant = getattr(route, "dependant", None)
    for dep in getattr(dependant, "dependencies", []) or []:
        fn = getattr(dep, "call", None)
        if fn is not None:
            names.add(_name_of(fn))
    for dep in getattr(route, "dependencies", []) or []:
        fn = getattr(dep, "dependency", None)
        if fn is not None:
            names.add(_name_of(fn))
    return names


def test_every_panel_route_is_owner_guarded():
    unguarded = []
    for router in _panel_routers():
        for route in router.routes:
            path = getattr(route, "path", "")
            if not path.startswith("/panel"):
                continue
            if path in DOORS:
                continue
            if OWNER_DEP not in _dependency_names(route):
                unguarded.append(f"{sorted(getattr(route, 'methods', []) or [])} {path}")
    assert not unguarded, (
        "ручки под /panel без require_owner — публичный префикс сделает их "
        f"открытыми миру: {unguarded}"
    )


def test_guard_matches_by_name_not_identity():
    """Сторож обязан пережить `importlib.reload` панельных модулей.

    Подделываем ровно то, что делает reload: другой объект функции с тем же
    модулем и именем. Сверка по identity здесь падала — по имени проходит.
    """
    from fastapi import Depends

    def clone(request=None, x_panels_key=None):  # pragma: no cover - не вызывается
        ...

    clone.__module__, clone.__qualname__ = OWNER_DEP

    class _Route:
        dependant = None
        dependencies = [Depends(clone)]

    assert OWNER_DEP in _dependency_names(_Route())


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
    assert DOORS <= set(panel_paths), (
        f"дверь исчезла из смонтированных ручек: {sorted(DOORS - set(panel_paths))}")


def test_main_wires_the_panel_login_redirect():
    """Обработчик отказа ставится явным вызовом, и забыть его легко: тесты
    поднимают своё приложение и о `app/main.py` ничего не знают. Без вызова
    панель снова отдаёт браузеру голый JSON — то самое, из чего нет выхода.

    Сверяем ИСХОДНИК, а не импортируем `app.main`: импорт тянет весь бэкенд с
    роутерами, планировщиком и сетью, и в юнит-прогоне это не тест, а запуск.
    """
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(
        encoding="utf-8")
    block = src.split("if panels_enabled():", 1)
    assert len(block) == 2, "блок монтирования панелей в app/main.py не найден"
    mounted = block[1].split("else:", 1)[0]
    assert "install_panel_auth_redirect(app)" in mounted, (
        "панели смонтированы, а обработчик отказа не поставлен — 401 снова "
        "станет тупиком для браузера")
