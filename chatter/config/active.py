"""Список активных клиентов (онбординг-дырка №3).

Раньше состав клиентов был дефолтом argparse ("demo,demo2"), а гардиан
запускал раннер без --personas — поэтому подключение нового клиента требовало
правки PowerShell-скрипта, то есть деплоя вместо онбординга.

Теперь состав живёт в chatter/clients/active.yaml. Гардиан не меняется вовсе:
он и так не передаёт --personas, а раннер берёт дефолт отсюда.
"""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

import yaml

ACTIVE_FILE = "active.yaml"
# Состав боевого деплоя до появления active.yaml. Нужен только как страховка:
# отсутствие нового файла не должно ронять прод при первом же рестарте.
LEGACY_PERSONAS = ["demo", "demo2"]


class ActiveClientsError(Exception):
    pass


def _parse(text: str, where: str) -> list[str]:
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ActiveClientsError(f"{where}: некорректный YAML ({exc})") from exc
    if not isinstance(raw, dict) or "clients" not in raw:
        raise ActiveClientsError(f"{where}: нужен ключ 'clients' со списком слагов")
    clients = raw["clients"]
    if not isinstance(clients, list) or not all(isinstance(c, str) for c in clients):
        raise ActiveClientsError(f"{where}: 'clients' должен быть списком строк")
    slugs = [c.strip() for c in clients if c and c.strip()]
    if not slugs:
        raise ActiveClientsError(
            f"{where}: список клиентов пуст — раннер не обслуживал бы никого")
    dupes = sorted({s for s in slugs if slugs.count(s) > 1})
    if dupes:
        raise ActiveClientsError(f"{where}: дубликаты в списке: {', '.join(dupes)}")
    return slugs


def resolve_personas(
    *, arg: str | None = None, clients_dir: Path | str,
    env: Mapping[str, str] | None = None,
) -> list[str]:
    """Состав клиентов. Приоритет: --personas > CHATTER_PERSONAS > active.yaml
    > legacy-дефолт. ПЕРВЫЙ slug — первичный (даёт allowlist и пути session/db).

    Битый/пустой active.yaml — громкая ошибка: тихий старт «ни с кем» выглядел
    бы как «Аня молчит», и причину искали бы в Telegram, а не в конфиге."""
    env = {} if env is None else env
    explicit = arg or env.get("CHATTER_PERSONAS")
    if explicit:
        slugs = [s.strip() for s in explicit.split(",") if s.strip()]
        if not slugs:
            raise ActiveClientsError("--personas/CHATTER_PERSONAS пуст")
        return slugs

    path = Path(clients_dir) / ACTIVE_FILE
    if not path.exists():
        return list(LEGACY_PERSONAS)
    return _parse(path.read_text(encoding="utf-8"), str(path))
