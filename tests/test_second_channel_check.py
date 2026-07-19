# -*- coding: utf-8 -*-
"""DEV-23: проверка второго канала обязана мерить ДОСТИЖИМОСТЬ, а не «служба Running».

Старый check_remote_status.ps1 печатал зелёное `[ok] Tailscale service Running`
при канале, в который невозможно войти (`Logged out`), не давал общего вердикта
и всегда возвращал код 0. Проверка, зелёная при мёртвом канале, ХУЖЕ отсутствия
проверки: перед правкой cloudflared она отвечает «второй канал есть», и
самоблокировка (SEV-1, 2026-07-15) повторяется.

Только пурные функции вердикта — ровно как test_chatter_watch_check /
test_ops_watchdog.
"""
import importlib.util as _ilu
import json
from pathlib import Path as _P

_spec = _ilu.spec_from_file_location(
    "second_channel_check",
    _P(__file__).resolve().parent.parent / "scripts" / "remote" / "second_channel_check.py")
sc = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(sc)


def _status(backend="Running", ips=("100.64.0.1",), peers=1, peer_online=True):
    return json.dumps({
        "BackendState": backend,
        "Self": {"TailscaleIPs": list(ips), "HostName": "PC-LOE"},
        "Peer": {
            f"key{i}": {"HostName": f"peer{i}", "Online": peer_online,
                        "TailscaleIPs": [f"100.64.0.{i + 2}"]}
            for i in range(peers)
        },
    })


# --- непригодный канал ------------------------------------------------------

def test_logged_out_is_unusable():
    """Боевое состояние на 2026-07-19: служба крутится, войти нельзя."""
    v = sc.second_channel_verdict(_status(backend="NeedsLogin"))
    assert v.usable is False
    assert "tailscale up" in v.reason.lower()


def test_stopped_backend_is_unusable():
    assert sc.second_channel_verdict(_status(backend="Stopped")).usable is False


def test_running_without_ip_is_unusable():
    """BackendState=Running без адреса в тайлнете — канала нет."""
    v = sc.second_channel_verdict(_status(ips=()))
    assert v.usable is False
    assert "адрес" in v.reason.lower() or "ip" in v.reason.lower()


def test_no_peers_is_not_usable():
    """У канала два конца. Если в тайлнете только эта машина, входить НЕОТКУДА —
    зелёный вердикт был бы той же ложью, что и «служба Running»."""
    v = sc.second_channel_verdict(_status(peers=0))
    assert v.usable is False
    assert "пир" in v.reason.lower() or "устройств" in v.reason.lower()


# --- пригодный канал --------------------------------------------------------

def test_logged_in_with_ip_and_peer_is_usable():
    v = sc.second_channel_verdict(_status())
    assert v.usable is True
    assert "100.64.0.1" in v.reason


def test_offline_peer_still_counts_as_reachable_endpoint():
    """Ноутбук может быть сейчас выключен — это не делает канал сломанным.
    Важно, что второй конец ЗАРЕГИСТРИРОВАН и подключится, когда понадобится."""
    v = sc.second_channel_verdict(_status(peers=2, peer_online=False))
    assert v.usable is True


# --- устойчивость -----------------------------------------------------------

def test_malformed_json_is_unknown_never_usable():
    for bad in ("", "не json", "{", "null", "[]"):
        v = sc.second_channel_verdict(bad)
        assert v.usable is False, f"мусор {bad!r} не должен давать зелёный"


def test_missing_keys_never_usable():
    assert sc.second_channel_verdict(json.dumps({})).usable is False


def test_verdict_is_never_usable_without_running_backend():
    """Свойство: что бы ни было в остальных полях, без Running — не годен."""
    for backend in ("NeedsLogin", "Stopped", "NoState", "Starting", ""):
        v = sc.second_channel_verdict(_status(backend=backend, peers=3))
        assert v.usable is False, backend


# --- код возврата (чтобы проверку можно было использовать как гейт) --------

def test_exit_code_zero_only_when_usable():
    assert sc.exit_code(sc.second_channel_verdict(_status())) == 0
    assert sc.exit_code(sc.second_channel_verdict(_status(backend="NeedsLogin"))) != 0
    assert sc.exit_code(sc.second_channel_verdict("мусор")) != 0


def test_summary_line_states_verdict_plainly():
    """Итог должен читаться одной строкой, а не собираться глазами из 15."""
    bad = sc.summary_line(sc.second_channel_verdict(_status(backend="NeedsLogin")))
    good = sc.summary_line(sc.second_channel_verdict(_status()))
    assert "НЕ ГОДЕН" in bad
    assert "ГОДЕН" in good and "НЕ ГОДЕН" not in good


def test_summary_mentions_the_rule_it_gates():
    """Человек, увидевший красное, должен сразу понять, что нельзя делать."""
    bad = sc.summary_line(sc.second_channel_verdict(_status(backend="NeedsLogin")))
    assert "cloudflared" in bad.lower()
