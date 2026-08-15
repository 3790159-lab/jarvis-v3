"""Отпечаток устройства Telethon прибит константами (перенос хоста).

Мотив. Telethon 1.44 собирает `InitConnectionRequest` из `platform.uname()`:

    if system.machine in ('x86_64', 'AMD64'):  default_device_model = 'PC 64bit'
    elif system.machine in ('i386','i686','x86'): default_device_model = 'PC 32bit'
    else:                                      default_device_model = system.machine
    default_system_version = re.sub(r'-.+','', system.release)
    app_version = app_version or self.__version__

То есть отпечаток менялся сам по трём независимым причинам:
  * `system_version` — при смене ОС ('11' на этом хосте → версия ядра на Linux);
  * `device_model`   — при смене архитектуры (x86_64 → aarch64 даёт 'aarch64');
  * `app_version`    — при ЛЮБОМ `pip upgrade telethon`, без смены хоста вообще.

Тесты держат ровно это: значения не зависят ни от платформы, ни от версии
библиотеки. Сеть не трогается — клиент подменён.
"""
from __future__ import annotations

import platform
from collections import namedtuple

import pytest

from chatter.telethon_identity import (
    APP_VERSION, DEVICE_MODEL, SYSTEM_VERSION, identity_kwargs,
)


class _FakeClient:
    """Ловит kwargs вместо конструирования настоящего TelegramClient."""
    last: dict | None = None

    def __init__(self, session, api_id, api_hash, **kwargs):
        _FakeClient.last = {"session": session, "api_id": api_id,
                            "api_hash": api_hash, **kwargs}


# namedtuple, а не platform.uname_result: в 3.14 у него `processor` вычисляется
# лениво и позиционно не принимается. Telethon читает только .machine/.release,
# поэтому утиной совместимости достаточно и тест не привязан к версии Python.
_Uname = namedtuple("_Uname", "system node release version machine processor")


def _uname(system, node, release, version, machine, processor):
    return _Uname(system, node, release, version, machine, processor)


PLATFORMS = [
    pytest.param(_uname("Windows", "pc-loe", "11", "10.0.26200", "AMD64", "Intel64"),
                 id="windows-x86_64"),
    pytest.param(_uname("Linux", "vps", "6.8.0", "#1 SMP", "x86_64", "x86_64"),
                 id="linux-x86_64"),
    pytest.param(_uname("Linux", "arm-vps", "6.5.0", "#1 SMP", "aarch64", ""),
                 id="linux-aarch64"),
    pytest.param(_uname("Darwin", "mac", "23.4.0", "Darwin K", "arm64", "arm"),
                 id="macos-arm64"),
]


@pytest.mark.parametrize("fake", PLATFORMS)
def test_identity_kwargs_do_not_depend_on_platform(monkeypatch, fake):
    """Ядро задачи: на любой ОС и архитектуре — те же три значения."""
    monkeypatch.setattr(platform, "uname", lambda: fake)
    assert identity_kwargs() == {
        "device_model": DEVICE_MODEL,
        "system_version": SYSTEM_VERSION,
        "app_version": APP_VERSION,
    }


def test_identity_kwargs_do_not_depend_on_telethon_version(monkeypatch):
    """`app_version` по умолчанию = telethon.__version__ — дрейфовал при
    апгрейде библиотеки без всякой смены хоста. Пин обязан это отвязать."""
    import telethon
    monkeypatch.setattr(telethon, "__version__", "9.9.9-fake", raising=False)
    assert identity_kwargs()["app_version"] == APP_VERSION
    assert "9.9.9" not in APP_VERSION


def test_constants_are_non_empty_strings():
    """Пустая строка обнулила бы пин: Telethon подставил бы дефолт обратно
    (`device_model or default_device_model`)."""
    for name, value in (("DEVICE_MODEL", DEVICE_MODEL),
                        ("SYSTEM_VERSION", SYSTEM_VERSION),
                        ("APP_VERSION", APP_VERSION)):
        assert isinstance(value, str) and value.strip(), f"{name} пуст — пин мёртв"


def test_identity_kwargs_returns_a_fresh_dict():
    """Вызывающие вправе домешивать своё (**identity_kwargs(), ...); общий
    мутируемый словарь протёк бы между клиентами."""
    a, b = identity_kwargs(), identity_kwargs()
    assert a == b and a is not b
    a["device_model"] = "испорчено"
    assert identity_kwargs()["device_model"] == DEVICE_MODEL


# --- проводка: отпечаток реально доезжает до конструктора клиента ------------
# «Константы объявлены» и «клиент их получил» — разные утверждения.

def test_login_make_client_passes_identity(monkeypatch):
    from chatter.telethon_login import make_client
    for fake in (PLATFORMS[0].values[0], PLATFORMS[2].values[0]):
        monkeypatch.setattr(platform, "uname", lambda f=fake: f)
        make_client("sess", "123", "hash", client_cls=_FakeClient)
        got = _FakeClient.last
        assert got["device_model"] == DEVICE_MODEL
        assert got["system_version"] == SYSTEM_VERSION
        assert got["app_version"] == APP_VERSION


def test_runner_builds_client_with_identity(monkeypatch):
    """telethon_run конструирует клиент сам (не через make_client) — сторож,
    чтобы пин не забыли на главном, ПРОДАКШН-пути."""
    import chatter.telethon_run as tr
    client = tr.build_client("sess-obj", "123", "hash", client_cls=_FakeClient)
    assert isinstance(client, _FakeClient)
    got = _FakeClient.last
    assert got["session"] == "sess-obj" and got["api_id"] == 123
    assert got["device_model"] == DEVICE_MODEL
    assert got["system_version"] == SYSTEM_VERSION
    assert got["app_version"] == APP_VERSION
