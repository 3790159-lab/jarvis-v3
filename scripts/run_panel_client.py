# -*- coding: utf-8 -*-
"""Поднять клиентскую панель ОТДЕЛЬНЫМ инстансом (один клиент = один процесс).

    python scripts/run_panel_client.py --slug yarina --port 8011

Что здесь важно и почему:

* ключ берётся из `JARVIS_PANELS_KEY_<SLUG>` и подставляется процессу как
  `JARVIS_PANELS_KEY`. В argv он не светится. Ключ владельца (`JARVIS_PANELS_KEY`
  из `.env`) сюда не годится — он открывает панель Джарвиса на 8010, и отдать
  его клиенту значит отдать ферму;
* ключ владельца читается из `.env` НАПРЯМУЮ, чтобы сверить с ним ключ
  инстанса. Ловушка не теоретическая: `.env` грузится с `override=False`, то
  есть ЗАБЫТАЯ переменная молча заменяется ключом владельца — без единой
  ошибки;
* пути клиента выводятся из слага (`.secrets/<slug>.db`,
  `state/chatter_heartbeat_<slug>.txt`), а не берутся из дефолтов дашборда:
  его дефолты — `volska` и `.secrets/demo.db`, то есть ЧУЖОЙ клиент;
* приложение — `app.panel_client`, а НЕ `app.main`: без фоновых задач и без
  `/panel/jarvis` (см. модуль, там замер).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

OWNER_KEY_VAR = "JARVIS_PANELS_KEY"
DEFAULT_PORT = 8011
ANY_INTERFACE = "0.0.0.0"
LOOPBACK = "127.0.0.1"
HOST_VAR = "PANEL_CLIENT_HOST"
# Путь собирается ПО ЧАСТЯМ намеренно. В литерале `"...\Tailscale\tailscale.exe"`
# экран `\t` однажды уже раскрылся в табуляцию ЕЩЁ ПРИ ЗАПИСИ ФАЙЛА, и raw-строка
# сохранила уже табуляцию: путь стал несуществующим, `tailnet_ip()` молча вернул
# "" — и инстанс ушёл на петлю, то есть стал недоступен тому, ради кого поднят.
# Здесь `\t` не появляется вовсе, поэтому раскрывать нечего.
TAILSCALE_EXE = os.path.join(r"C:\Program Files", "Tailscale", "tailscale.exe")


def tailnet_ip(exe: str = TAILSCALE_EXE) -> str:
    """IPv4 машины в тайнете, или пустая строка. Best-effort по замыслу:
    отсутствие tailscale не должно ронять запуск, оно должно уводить на
    петлю."""
    import subprocess
    try:
        r = subprocess.run([exe, "ip", "-4"], capture_output=True, timeout=10)
    except Exception:
        return ""
    if r.returncode != 0:
        return ""
    for line in (r.stdout or b"").decode("ascii", "replace").splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def resolve_client_host(explicit=None, environ=None, ip="",
                        allow_any=False):
    """(host, problem). Адрес инстанса клиента НЕ берётся из
    `app.backend_bind.resolve_bind_host`.

    Причина названа замером: у основного бэкенда `JARVIS_BACKEND_HOST=0.0.0.0`
    — для него это осознанно (он и должен отвечать в тайнете и на петле). Для
    панели, ключ от которой уходит стороннему человеку, `0.0.0.0` означает
    «отвечаю всей локальной сети», и единственной защитой остаётся ключ.
    Поэтому здесь свой резолвер: явный адрес -> PANEL_CLIENT_HOST -> тайнет ->
    петля. `0.0.0.0` — только осознанным флагом.
    """
    environ = os.environ if environ is None else environ
    host = (explicit or environ.get(HOST_VAR) or "").strip()
    if not host:
        host = ip.strip() or LOOPBACK
    if host == ANY_INTERFACE and not allow_any:
        return None, (
            "адрес %s открыл бы панель клиента ВСЕЙ локальной сети; "
            "нужен адрес тайнета или петля (--allow-any-interface, если это "
            "и правда нужно)" % ANY_INTERFACE)
    return host, None


def env_file_var(name: str, root: Path = _ROOT) -> str:
    """Значение переменной ИЗ ФАЙЛА `.env`, до всякого bootstrap'а.

    Читаем файл, а не `os.environ`: после `app.env_bootstrap` ключ владельца и
    ключ инстанса сливаются в одно значение, и сверить их станет невозможно
    ровно тогда, когда это важнее всего.

    Имя сверяется ВМЕСТЕ со знаком `=`. Это не педантизм: `JARVIS_PANELS_KEY`
    — префикс `JARVIS_PANELS_KEY_YARINA`, и совпадение по префиксу отдало бы
    стороннему человеку ключ владельца.
    """
    path = root / ".env"
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return ""
    prefix = name + "="
    for line in text.splitlines():
        if line.startswith(prefix):
            return line.split("=", 1)[1].strip()
    return ""


def owner_key_from_env_file(root: Path = _ROOT) -> str:
    """Ключ владельца из `.env`. Тонкая обёртка: имя названо в одном месте."""
    return env_file_var(OWNER_KEY_VAR, root=root)


def build_instance_env(slug: str, environ=None, root: Path = _ROOT) -> dict:
    """Окружение инстанса. Чистая функция — её и проверяют сторожа."""
    environ = os.environ if environ is None else environ
    var = "JARVIS_PANELS_KEY_%s" % slug.upper()
    # Два источника, и порядок важен. Переменная процесса первая — это способ
    # поднять инстанс, не трогая `.env`. Файл второй, потому что владелец
    # держит ключи клиентов именно там; без чтения файла fail-closed отказывал
    # при ВЕРНО настроенном ключе, и отказ звучал как «ключ не задан».
    # Читаем ТОЧНОЕ имя `JARVIS_PANELS_KEY_<SLUG>`, а не общее: общее в `.env`
    # — это ключ владельца, открывающий панель Джарвиса на 8010.
    key = (environ.get(var) or "").strip() or env_file_var(var, root=root)
    return {
        "JARVIS_PANELS_KEY": key,
        "TAMAPI_DB": str(root / ".secrets" / ("%s.db" % slug)),
        "TAMAPI_SLUG": slug,
        "TAMAPI_HEARTBEAT": str(root / "state" / ("chatter_heartbeat_%s.txt" % slug)),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--slug", required=True, help="слаг клиента (yarina, volska, ...)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--host", default=None,
                    help="адрес бинда; по умолчанию тайнет, иначе петля")
    ap.add_argument("--allow-any-interface", action="store_true",
                    help="разрешить 0.0.0.0 ОСОЗНАННО (панель увидит вся сеть)")
    a = ap.parse_args(argv)

    owner_key = owner_key_from_env_file()
    # Собираем ДО bootstrap'а: иначе `.env` уже подмешался и сверка ослепла.
    instance = build_instance_env(a.slug)

    import app.env_bootstrap  # noqa: F401  side-effect: .env -> os.environ
    from app.panel_client import build_app, instance_env_problems

    problems = instance_env_problems(instance, owner_key=owner_key)
    if problems:
        # DEV-18: молчаливый старт на кривом окружении — это панель, которая
        # показывает чужого клиента, и никто об этом не узнает.
        print("[panel_client] ОТКАЗ, инстанс не поднят:")
        for p in problems:
            print("  * %s" % p)
        print("  ключ задаётся переменной JARVIS_PANELS_KEY_%s" % a.slug.upper())
        return 1

    host, problem = resolve_client_host(a.host, ip=tailnet_ip(),
                                        allow_any=a.allow_any_interface)
    if problem:
        print("[panel_client] ОТКАЗ, инстанс не поднят:")
        print("  * %s" % problem)
        return 1

    os.environ.update(instance)
    print("[panel_client] %s -> %s:%s  (db=%s)"
          % (a.slug, host, a.port, instance["TAMAPI_DB"]))

    import uvicorn
    uvicorn.run(build_app(), host=host, port=a.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
