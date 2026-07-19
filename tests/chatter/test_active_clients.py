"""Онбординг-дырка №3: список клиентов живёт в конфиге, а не в аргументах.

Гардиан запускал раннер с зашитым дефолтом demo,demo2, поэтому подключение
нового клиента означало правку PowerShell-скрипта — то есть деплой, а не
онбординг. Теперь гардиан не меняется вовсе: он и так не передаёт
--personas, а дефолт берётся из chatter/clients/active.yaml.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from chatter.config.active import ActiveClientsError, resolve_personas

ACTIVE = "active.yaml"


def _dir(tmp_path, text: str | None = None) -> Path:
    d = tmp_path / "clients"
    d.mkdir(parents=True, exist_ok=True)
    if text is not None:
        (d / ACTIVE).write_text(text, encoding="utf-8")
    return d


def test_reads_list_from_config(tmp_path):
    d = _dir(tmp_path, "clients:\n  - acme\n  - beta\n")
    assert resolve_personas(clients_dir=d, env={}) == ["acme", "beta"]


def test_first_entry_is_primary(tmp_path):
    """Первый — первичный: он даёт allowlist и определяет пути session/db."""
    d = _dir(tmp_path, "clients:\n  - acme\n  - beta\n")
    assert resolve_personas(clients_dir=d, env={})[0] == "acme"


def test_explicit_arg_wins(tmp_path):
    d = _dir(tmp_path, "clients:\n  - acme\n")
    assert resolve_personas(arg="beta,gamma", clients_dir=d, env={}) == ["beta", "gamma"]


def test_env_wins_over_file(tmp_path):
    d = _dir(tmp_path, "clients:\n  - acme\n")
    assert resolve_personas(clients_dir=d, env={"CHATTER_PERSONAS": "beta"}) == ["beta"]


def test_missing_file_falls_back_to_legacy(tmp_path):
    """Боевой деплой не должен упасть из-за отсутствия нового файла."""
    d = _dir(tmp_path)
    assert resolve_personas(clients_dir=d, env={}) == ["demo", "demo2"]


def test_empty_list_is_loud(tmp_path):
    """Пустой список = Аня молчит вообще. Молча стартовать с пустотой нельзя."""
    d = _dir(tmp_path, "clients: []\n")
    with pytest.raises(ActiveClientsError):
        resolve_personas(clients_dir=d, env={})


def test_malformed_file_is_loud(tmp_path):
    d = _dir(tmp_path, "clients: [broken\n")
    with pytest.raises(ActiveClientsError):
        resolve_personas(clients_dir=d, env={})


def test_wrong_shape_is_loud(tmp_path):
    d = _dir(tmp_path, "clients: acme\n")
    with pytest.raises(ActiveClientsError):
        resolve_personas(clients_dir=d, env={})


def test_duplicates_rejected(tmp_path):
    """Один slug дважды = две персоны на один каталог, тихая путаница."""
    d = _dir(tmp_path, "clients:\n  - acme\n  - acme\n")
    with pytest.raises(ActiveClientsError):
        resolve_personas(clients_dir=d, env={})


def test_shipped_active_yaml_matches_current_prod():
    """Файл в репозитории обязан описывать то, что крутится сейчас, иначе
    первый же рестарт гардиана поменял бы состав клиентов в проде."""
    d = Path(__file__).resolve().parents[2] / "chatter" / "clients"
    assert (d / ACTIVE).exists()
    assert resolve_personas(clients_dir=d, env={}) == ["demo", "demo2"]


def test_every_listed_client_exists():
    """Опечатка в active.yaml не должна вскрываться падением в проде."""
    d = Path(__file__).resolve().parents[2] / "chatter" / "clients"
    for slug in resolve_personas(clients_dir=d, env={}):
        assert (d / slug).is_dir(), f"active.yaml ссылается на несуществующий {slug}"
