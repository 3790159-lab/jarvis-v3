# -*- coding: utf-8 -*-
"""Клиентская панель ОТДЕЛЬНЫМ инстансом: только дашборд, ничего больше.

Спека: docs/superpowers/specs/2026-08-18-yarina-client-panel-instance.md.
Ключ от этого приложения уходит СТОРОННЕМУ человеку, поэтому оно собрано не
«как `app.main`, только с другим TAMAPI_DB». Замер назвал две причины, по
которым так нельзя:

1. `app/main.py:308-309` вешает на старте `_watchdog_loop()`, зовущий
   `restart_bot_if_dead()` каждые 60 с. Второй инстанс — второй хозяин у
   живого бота. Этот класс уже стоил прода 18.08 (DEV-38) и 13 ч 42 мин
   простоя Ольги 16.08.
2. `app/main.py:236-244` монтирует клиентский дашборд и `/panel/jarvis` одним
   блоком, а `require_owner` у них на ОДНОМ ключе. Ключ клиента открыл бы
   PID'ы, ветки и сроки ключей фермы.

Поэтому здесь ровно два роутера и ни одного обработчика старта. Закрыто ПО
СОСТАВУ: то, чего в приложении нет, нельзя открыть опечаткой в env.
"""
from __future__ import annotations

from fastapi import FastAPI

# Переменные, которые ОБЯЗАН задать запускающий. Дефолты у них есть
# (`tamapi_dashboard._db_path` -> `.secrets/demo.db`, `_slug` -> `volska`), и в
# этом вся опасность: инстанс, поднятый без них, молча показал бы клиенту
# переписку ОЛЬГИ. Здесь дефолт = отказ стартовать.
REQUIRED_INSTANCE_VARS = ("TAMAPI_DB", "TAMAPI_SLUG")


def instance_env_problems(env, *, owner_key: str = "") -> list[str]:
    """Что не так с окружением инстанса. Пустой список = можно поднимать.

    Чистая функция: решение отделено от запуска, потому что проверить надо
    именно РЕШЕНИЕ, а не то, что процесс как-то поднялся.
    """
    problems: list[str] = []
    key = (env.get("JARVIS_PANELS_KEY") or "").strip()
    if not key:
        problems.append(
            "JARVIS_PANELS_KEY не задан: панель закрыта по умолчанию и без "
            "ключа не поднимается")
    elif owner_key and key == owner_key:
        # Главная ловушка: `.env` грузится с override=False, поэтому НЕзаданный
        # ключ процесса молча заменяется ключом ВЛАДЕЛЬЦА из `.env`. А тот же
        # ключ открывает панель Джарвиса на 8010. То есть забытая переменная
        # выдала бы стороннему человеку доступ к ферме — без единой ошибки.
        problems.append(
            "JARVIS_PANELS_KEY инстанса СОВПАДАЕТ с ключом владельца из .env: "
            "этот ключ открывает и панель Джарвиса на основном бэкенде")
    for name in REQUIRED_INSTANCE_VARS:
        if not (env.get(name) or "").strip():
            problems.append(
                "%s не задан: без него панель показала бы клиента по умолчанию "
                "(volska/.secrets/demo.db), то есть ЧУЖУЮ переписку" % name)
    return problems


def build_app() -> FastAPI:
    """Собрать приложение. Импорты внутри — чтобы модуль можно было
    импортировать (и проверять) без поднятого окружения."""
    from app.routers.panels_auth import (install_panel_auth_redirect,
                                         panels_enabled)
    from app.routers.panels_auth import router as login_router
    from app.routers.tamapi_dashboard import router as tamapi_router

    if not panels_enabled():
        raise RuntimeError("JARVIS_PANELS_KEY не задан — панель fail-closed")

    # Схема API наружу не отдаётся: клиенту она не нужна, а перечень ручек —
    # это карта для того, кто ключ подберёт.
    api = FastAPI(title="TAMAPI client panel",
                  docs_url=None, redoc_url=None, openapi_url=None)
    api.include_router(login_router)
    api.include_router(tamapi_router)
    # Отказ обязан иметь выход: браузеру — форма входа, машине — прежний 401.
    # Ставится там же, где монтируются роутеры, как и в app/main.py.
    install_panel_auth_redirect(api)
    return api
