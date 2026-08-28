# -*- coding: utf-8 -*-
"""Публичная дверь: ПЕРВАЯ точка входа в систему снаружи.

Система дожила до сегодня без единой публичной HTTP-двери — в живом ingress
только `ssh`, `rdp` и `http_status:404`. Это не недоделка: Telegram был
единственным каналом, и он позволял жить поллингом. Веб-канал так не умеет.

ЦЕНА ОШИБКИ ЗДЕСЬ ДРУГАЯ. У панели она «чужой увидел». Здесь — «чужой ПИШЕТ в
диалоги клиента». Поэтому каждое решение ниже фейл-клозед, и у каждого свой
сторож.

ЧЕГО ЭТА ДВЕРЬ НЕ ДЕЛАЕТ. Она не знает ни про очередь, ни про схему, ни про
мозг: принятое уходит в `sink`, переданный при сборке. Вебхук Meta — тоже не
она: там чужая аутентификация (`X-Hub-Signature-256`) и чужой график ревью, а
здесь оба конца наши, и ровно поэтому веб делается раньше и без чужого ревью.
"""
from __future__ import annotations

import hmac
import json
import os
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Mapping
from typing import Any, Callable, Deque

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

# Путь ручки — КОНСТАНТА, а не строка по месту: адрес двери наружу обязан
# читаться в одном месте, иначе «поменяли в роутере, забыли в пробе».
PUBLIC_DOOR_PATH = "/public/door/message"

ENV_KEY = "JARVIS_PUBLIC_DOOR_KEY"
DEFAULT_TRAIL_PATH = "state/public_door.jsonl"

CLIENT_HEADER = "X-Door-Client"
TOKEN_HEADER = "X-Door-Token"

# 32 — не круглое число «для солидности»: короче этого токен перебирается
# быстрее, чем мы успеем заметить по следу.
MIN_TOKEN_LEN = 32

DEFAULT_MAX_BODY_BYTES = 64 * 1024
DEFAULT_PER_CLIENT_PER_MINUTE = 60
DEFAULT_PER_IP_PER_MINUTE = 120

# ОДНО тело на ВСЕ отказы аутентификации. Разные ответы на «нет такого
# клиента», «клиент выключен» и «токен не тот» превращают дверь в оракул: по
# ним перебирают, не имея ни одного валидного токена.
REFUSAL_BODY: dict[str, str] = {"error": "refused"}
REFUSAL_STATUS = 401


class DoorMisconfigured(RuntimeError):
    """Дверь настроена наполовину.

    Отдельный тип, а не голый RuntimeError: это состояние НАСТРОЙКИ, и
    отличать его от аварии времени выполнения нужно и человеку, и пробе.
    """


@dataclass
class DoorConfig:
    """Настройка двери. Клиент -> {token, enabled}."""

    clients: dict[str, dict[str, Any]] = field(default_factory=dict)
    trail_path: str | Path = DEFAULT_TRAIL_PATH
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
    per_client_per_minute: int = DEFAULT_PER_CLIENT_PER_MINUTE
    per_ip_per_minute: int = DEFAULT_PER_IP_PER_MINUTE


class _Window:
    """Скользящее окно в минуту, на ключ.

    Своё, а не библиотека: одна структура, которую видно целиком, дешевле
    зависимости, которую придётся объяснять на приёмке.
    """

    def __init__(self) -> None:
        self._hits: dict[str, Deque[float]] = {}

    def allow(self, key: str, limit: int, now: float) -> bool:
        q = self._hits.setdefault(key, deque())
        cutoff = now - 60.0
        while q and q[0] <= cutoff:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


def _trail(path: str | Path, *, client: str | None, outcome: int, reason: str,
           now: float | None = None) -> None:
    """Строка следа на КАЖДЫЙ исход, включая отказы.

    Дверь наружу без следа — ровно та тишина, из-за которой 26.08 сутки никто
    не знал об обрыве.

    В строке НЕТ ни токена, ни тела запроса. След, утекающий секретом, — это
    вторая копия секрета в месте, которое никто не охраняет.
    """
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({
            "ts": round(now if now is not None else time.time(), 3),
            "client": client or "?",
            "outcome": int(outcome),
            "reason": reason,
        }, ensure_ascii=False)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        # След не имеет права уронить дверь: иначе полный диск превращается в
        # отказ обслуживания. Но и молчать нельзя — это видно по отсутствию
        # строк, что и есть сигнал для пробы.
        pass


def _validate(config: DoorConfig) -> None:
    """Полу-настроенная дверь — ОТКАЗ, а не «как-нибудь».

    Каждый отказ НАЗЫВАЕТ виноватого: настройка, про которую сказано «что-то не
    так», чинится перебором.
    """
    if not isinstance(config.clients, dict) or not config.clients:
        raise DoorMisconfigured(
            "дверь включена (%s задан), но список клиентов пуст: принимать "
            "было бы не от кого и не для кого" % ENV_KEY)
    for slug, spec in sorted(config.clients.items()):
        if not isinstance(spec, dict):
            raise DoorMisconfigured(
                "клиент %r: ждём словарь {token, enabled}, пришло %s"
                % (slug, type(spec).__name__))
        token = spec.get("token")
        if not isinstance(token, str) or not token:
            raise DoorMisconfigured("клиент %r объявлен без токена" % slug)
        if len(token) < MIN_TOKEN_LEN:
            raise DoorMisconfigured(
                "клиент %r: токен короче %d символов (%d) — такой перебирается "
                "быстрее, чем мы заметим по следу"
                % (slug, MIN_TOKEN_LEN, len(token)))
    if int(config.max_body_bytes) <= 0:
        raise DoorMisconfigured("max_body_bytes обязан быть больше нуля")
    if int(config.per_client_per_minute) <= 0 or int(config.per_ip_per_minute) <= 0:
        raise DoorMisconfigured("лимиты обязаны быть больше нуля")


async def _read_capped(request: Request, cap: int) -> bytes | None:
    """Прочитать тело, оборвавшись на потолке. `None` — потолок пробит.

    Читаем ПОТОКОМ и считаем байты сами. `await request.body()` втянул бы всё
    целиком, и один запрос ронял бы процесс — а `Content-Length` присылает та
    сторона, то есть верить ему нельзя.
    """
    total = 0
    chunks: list[bytes] = []
    async for chunk in request.stream():
        total += len(chunk)
        if total > cap:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def as_config(config) -> DoorConfig:
    """Принять настройку И словарём, И датаклассом.

    Словарём — потому что настройка приезжает из `.env`/YAML, и заставлять
    вызывающего собирать датакласс значит завести второе место, где список
    полей обязан совпадать. Датаклассом — потому что внутри с ним удобнее.

    Приведение здесь ОДНО на весь модуль: два места, читающие настройку
    по-разному, — это ровно тот класс, где «два числа на одну вещь».
    """
    if config is None:
        raise DoorMisconfigured("дверь включена, а настройки нет вовсе")
    if isinstance(config, DoorConfig):
        return config
    if not isinstance(config, Mapping):
        raise DoorMisconfigured(
            "настройка двери: ждём словарь или DoorConfig, пришло %s"
            % type(config).__name__)

    def _int(key: str, default: int) -> int:
        raw_value = config.get(key, default)
        try:
            return int(raw_value)
        except (TypeError, ValueError):
            raise DoorMisconfigured(
                "настройка двери: %s обязан быть целым, пришло %r"
                % (key, raw_value)) from None

    return DoorConfig(
        clients=config.get("clients") if isinstance(config.get("clients"), dict) else {},
        trail_path=config.get("trail_path", DEFAULT_TRAIL_PATH),
        max_body_bytes=_int("max_body_bytes", DEFAULT_MAX_BODY_BYTES),
        per_client_per_minute=_int("per_client_per_minute", DEFAULT_PER_CLIENT_PER_MINUTE),
        per_ip_per_minute=_int("per_ip_per_minute", DEFAULT_PER_IP_PER_MINUTE),
    )


def build_public_door(config, sink: Callable[[dict], None]) -> APIRouter:
    """Собрать роутер двери. На полу-настройке — ОТКАЗ (см. `_validate`)."""
    config = as_config(config)
    if sink is None:
        raise DoorMisconfigured(
            "дверь включена, а приёмника нет: принятое некуда деть, и "
            "принимать значило бы терять")
    _validate(config)

    router = APIRouter()
    windows = _Window()

    @router.post(PUBLIC_DOOR_PATH)
    async def receive(request: Request):     # noqa: ANN202 — форма FastAPI
        now = time.time()
        trail_path = config.trail_path
        client = (request.headers.get(CLIENT_HEADER) or "").strip() or None

        # 1. ПОТОЛОК ТЕЛА — раньше всего остального, и раньше парсера.
        #    Разбор неограниченного тела это способ уронить процесс одним
        #    запросом, а он не требует ни токена, ни клиента.
        raw = await _read_capped(request, int(config.max_body_bytes))
        if raw is None:
            _trail(trail_path, client=client, outcome=413,
                   reason="body_over_cap", now=now)
            return JSONResponse({"error": "too_large"}, status_code=413)

        # 2. АУТЕНТИФИКАЦИЯ. Три разных «нет» отвечают ОДИНАКОВО.
        spec = config.clients.get(client) if client else None
        token = request.headers.get(TOKEN_HEADER) or ""
        expected = (spec or {}).get("token") or ""
        # compare_digest, а не `==`: сравнение строк утекает по времени, и по
        # этой утечке токен подбирается посимвольно.
        ok_token = bool(expected) and hmac.compare_digest(token, expected)
        enabled = bool((spec or {}).get("enabled", True))
        if spec is None or not enabled or not ok_token:
            reason = ("unknown_client" if spec is None
                      else "client_disabled" if not enabled else "bad_token")
            # Причина пишется в СЛЕД, но не в ответ: нам она нужна, чужому нет.
            _trail(trail_path, client=client, outcome=REFUSAL_STATUS,
                   reason=reason, now=now)
            return JSONResponse(dict(REFUSAL_BODY), status_code=REFUSAL_STATUS)

        # 3. ЛИМИТЫ. На клиента И на адрес: только на клиента — обходится
        #    сменой клиента, только на адрес — одним клиентом за NAT-ом.
        peer_ip = getattr(getattr(request, "client", None), "host", None) or "?"
        if not windows.allow("c:%s" % client, int(config.per_client_per_minute), now):
            _trail(trail_path, client=client, outcome=429,
                   reason="client_flood", now=now)
            return JSONResponse({"error": "slow_down"}, status_code=429,
                                headers={"Retry-After": "60"})
        if not windows.allow("i:%s" % peer_ip, int(config.per_ip_per_minute), now):
            _trail(trail_path, client=client, outcome=429,
                   reason="ip_flood", now=now)
            return JSONResponse({"error": "slow_down"}, status_code=429,
                                headers={"Retry-After": "60"})

        # 4. РАЗБОР. Только теперь, и только ограниченного тела.
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            _trail(trail_path, client=client, outcome=400,
                   reason="bad_json", now=now)
            return JSONResponse({"error": "bad_json"}, status_code=400)

        # 5. ПРИЁМНИК. Упал — отвечаем 503 и НЕ пишем приём как успешный:
        #    иначе «202» означало бы «мы приняли», когда никто не принял.
        envelope = {"client": client, "payload": payload}
        try:
            sink(envelope)
        except Exception:
            _trail(trail_path, client=client, outcome=503,
                   reason="sink_failed", now=now)
            return JSONResponse({"error": "unavailable"}, status_code=503)

        _trail(trail_path, client=client, outcome=202, reason="accepted", now=now)
        return JSONResponse({"status": "accepted"}, status_code=202)

    return router


def door_enabled(env: dict[str, str] | None = None) -> bool:
    """Дверь существует, только если задан ключ. По умолчанию её НЕТ."""
    src = os.environ if env is None else env
    return bool((src.get(ENV_KEY) or "").strip())


def install_public_door(app, *, config: DoorConfig | None = None,
                        sink: Callable[[dict], None] | None = None,
                        env: dict[str, str] | None = None) -> bool:
    """Смонтировать дверь — ТОЛЬКО при заданном ключе.

    Не «смонтировать и отвечать 404»: маршрута не должно быть вовсе. Разница
    видна снаружи и она содержательная — 404 у смонтированного роута говорит
    «дверь есть, ручки нет», а нам нужно «двери нет».

    Тот же образец, которым живут панели (`JARVIS_PANELS_KEY`): мерж арки не
    открывает наружу ничего сам по себе. Открывает владелец, отдельным
    действием.
    """
    if not door_enabled(env):
        return False
    app.include_router(build_public_door(config, sink))
    return True
