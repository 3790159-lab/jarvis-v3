"""Панель Джарвиса не имеет права занимать event loop собой.

Инцидент 13.08, приёмка деплоя части A. `panels_mobile_check` прошёл два экрана
и упал на третьем с `ERR_CONNECTION_REFUSED`; в логе гардиана в ту же секунду —
`backend DOWN - restarting`. Крэша не было: `/panel/jarvis` собирается 6.8 с
(`jarvis_farm.snapshot()` — git по четырём десяткам worktree, `Get-ScheduledTask`,
psutil), и всё это время ручка СИНХРОННО сидела в `async def`. Пока она сидит,
event loop не отдаёт НИЧЕГО — включая `/health`. Гардиан бэкенда пингует
`/health` каждые 15 с с таймаутом 3 с и убивает процесс на ПЕРВОМ провале, без
дебаунса. То есть открытие панели с телефона роняло прод, и роняло тем вернее,
чем больше в дереве веток.

Сторож меряет СВОЙСТВО, а не реализацию: пока висит `/panel/jarvis`, соседняя
ручка обязана ответить быстро. Способ развязки (`def` вместо `async def`,
`run_in_threadpool`, свой executor) тест не выбирает — любой годится.
"""
from __future__ import annotations

import asyncio
import importlib
import time

import httpx
import pytest
from fastapi import FastAPI

KEY = "test-owner-key"

# Сколько «собирается» ферма в тесте. Реальные 6.8 с ждать незачем — важно лишь
# то, что соседняя ручка отвечает НЕ дожидаясь конца сборки.
BLOCK_S = 0.6
# Порог для соседа. Гардиан даёт 3 с при сборке 6.8 с — та же пропорция.
PROBE_MAX_S = BLOCK_S / 3
# След подменённой фермы: виден и в HTML, и в JSON.
MARKER = "ferma-pidmineno"


def _fake_snapshot(farm):
    """Снимок ровно той формы, что читает ручка, но без единого git-вызова."""
    ext = farm.Row(key="external", label="Зовнішній сторож", state="ok",
                   detail=MARKER)
    return {
        "collected_at": time.time(),
        "external": ext,
        "processes": [farm.Row(key="backend", label="backend", state="ok")],
        "guardians": [farm.Row(key="g", label="гардіан", state="ok")],
        "tasks": [farm.Row(key="t", label="таск", state="ok")],
        "arcs": [],
        "events": [],
        "keys": [],
    }


@pytest.fixture()
def api(monkeypatch):
    monkeypatch.setenv("JARVIS_PANELS_KEY", KEY)

    import app.routers.panels_auth as pa
    import app.routers.jarvis_panel as jp
    for m in (pa, jp):
        importlib.reload(m)

    def slow_snapshot():
        time.sleep(BLOCK_S)          # ровно то, чем занят настоящий snapshot()
        return _fake_snapshot(jp.F)

    monkeypatch.setattr(jp.F, "snapshot", slow_snapshot)

    app = FastAPI()
    app.include_router(pa.router)
    app.include_router(jp.router)

    @app.get("/health")
    async def health():           # то же, что пингует гардиан бэкенда
        return {"ok": True}

    return app


def _probe_while_panel_renders(app, path: str) -> tuple[float, int]:
    """Возвращает (самый долгий простой event loop'а, статус страницы панели).

    Замерять «сколько ждал /health», запуская его ПОСЛЕ старта страницы, нельзя:
    `await` до замера сам упирается в блокировку, и секундомер стартует, когда
    всё уже кончилось — такой тест зелен на сломанном коде (проверено фактом).
    Меряем биение: корутина, которая обязана просыпаться каждые 10 мс. Её самый
    длинный пропуск и есть время, в которое гардиан не получил бы ответа.
    """
    async def run():
        gaps: list[float] = []
        stopping = asyncio.Event()

        async def heartbeat():
            prev = time.perf_counter()
            while not stopping.is_set():
                await asyncio.sleep(0.01)
                now = time.perf_counter()
                gaps.append(now - prev)
                prev = now

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://panel.test") as c:
            hb = asyncio.create_task(heartbeat())
            await asyncio.sleep(0.05)          # биение набрало ритм
            resp = await c.get(path, headers={"X-Panels-Key": KEY}, timeout=30)
            h = await c.get("/health", timeout=30)
            stopping.set()
            await hb
            assert h.status_code == 200
            # Подменённая ферма ОБЯЗАНА доехать до ответа: не доехала бы —
            # ручка собирала бы настоящий снимок, «простоя нет» означало бы
            # лишь то, что тест меряет пустоту.
            assert MARKER in resp.text, "в ответе нет следа подменённой фермы"
            return max(gaps), resp.status_code

    return asyncio.run(run())


def test_loop_stays_free_while_jarvis_panel_renders(api):
    stall, status = _probe_while_panel_renders(api, "/panel/jarvis")
    assert status == 200
    assert stall < PROBE_MAX_S, (
        f"event loop простоял {stall:.2f} с, пока рисовалась панель — гардиан "
        f"(пинг /health, таймаут 3 с, без дебаунса) на этом убивает бэкенд")


def test_loop_stays_free_while_snapshot_api_runs(api):
    """Та же ферма в JSON — отдельный вход в ту же сборку."""
    stall, status = _probe_while_panel_renders(api, "/panel/jarvis/api/snapshot")
    assert status == 200
    assert stall < PROBE_MAX_S, (
        f"event loop простоял {stall:.2f} с на /api/snapshot")
