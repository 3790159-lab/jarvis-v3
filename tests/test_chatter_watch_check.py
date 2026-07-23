# -*- coding: utf-8 -*-
"""chatter_watch_check — the standalone stdlib-only DOWN alerter for the chatter
Telethon runner (invoked by JarvisChatterGuardian). Only the pure decision
functions are unit-tested here; the urllib TG-send and marker file IO are
stdlib-only and exercised live by the guardian, exactly like
test_boot_watch / test_ops_watchdog."""
import importlib.util as _ilu
from pathlib import Path as _P

_spec = _ilu.spec_from_file_location(
    "chatter_watch_check",
    _P(__file__).resolve().parent.parent / "scripts" / "chatter_watch_check.py")
cw = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(cw)


def test_heartbeat_fresh_true_only_when_recent_and_numeric():
    now = 1000.0
    assert cw.is_heartbeat_fresh("970", now=now, max_age=60) is True    # 30s old
    assert cw.is_heartbeat_fresh("939", now=now, max_age=60) is False   # 61s old
    assert cw.is_heartbeat_fresh("", now=now, max_age=60) is False
    assert cw.is_heartbeat_fresh("garbage", now=now, max_age=60) is False
    assert cw.is_heartbeat_fresh(None, now=now, max_age=60) is False


def test_should_alert_only_when_down_and_cooldown_elapsed():
    now = 10_000.0
    # down + never alerted -> alert
    assert cw.should_alert(is_down=True, last_alert_ts=None, now=now, cooldown=3600) is True
    # down + alerted 10 min ago (cooldown 1h) -> suppressed
    assert cw.should_alert(is_down=True, last_alert_ts=now - 600, now=now, cooldown=3600) is False
    # down + alerted 2h ago -> alert again
    assert cw.should_alert(is_down=True, last_alert_ts=now - 7200, now=now, cooldown=3600) is True
    # healthy -> never alert regardless of history
    assert cw.should_alert(is_down=False, last_alert_ts=None, now=now, cooldown=3600) is False


def test_alert_text_mentions_chatter_and_recovery():
    txt = cw.alert_text()
    assert "chatter" in txt.lower()
    assert "JarvisChatterGuardian" in txt or "guardian" in txt.lower()


def test_should_notify_recovery_only_when_up_after_a_down_alert_was_sent():
    # The operator got a 🔴; the runner is fresh again -> he MUST get the paired ✅,
    # otherwise he never learns when it is safe to stop worrying.
    assert cw.should_notify_recovery(is_down=False, alerted=True) is True
    # Never alerted (quiet, healthy box) -> a ✅ out of nowhere is noise.
    assert cw.should_notify_recovery(is_down=False, alerted=False) is False
    # Still down -> not recovered, no matter the history.
    assert cw.should_notify_recovery(is_down=True, alerted=True) is False
    assert cw.should_notify_recovery(is_down=True, alerted=False) is False


def test_recovery_text_is_a_green_paired_confirmation():
    txt = cw.recovery_text()
    assert "✅" in txt
    assert "chatter" in txt.lower()


def test_resolve_is_down_trusts_the_guardians_verdict_over_the_heartbeat():
    now = 10_000.0
    fresh, stale = "9990", "9000"
    # THE DRILL BUG: the guardian declares DOWN on a dead PROCESS after ~90s of
    # debounce, while the heartbeat file it left behind is still < 180s old and
    # therefore "fresh". The alerter must not overrule the guardian and stay
    # silent — that is exactly how a real 85s outage went unannounced.
    assert cw.resolve_is_down("down", fresh, now=now, max_age=180) is True
    # Symmetrically: the guardian saw the runner alive (process + fresh beat).
    # A heartbeat that has not been rewritten yet must not fake an outage.
    assert cw.resolve_is_down("up", stale, now=now, max_age=180) is False
    # No verdict passed (standalone / external invocation) -> derive as before.
    assert cw.resolve_is_down(None, stale, now=now, max_age=180) is True
    assert cw.resolve_is_down(None, fresh, now=now, max_age=180) is False


def test_bot_token_prefers_env_enc(tmp_path, monkeypatch):
    """P1P2 слой процесса: после cutover plaintext .env шредится (§6 п.9) —
    алертер обязан уметь взять токен из .env.enc, иначе онемеет ровно тогда,
    когда нужен. Machine-scope DPAPI + entropy, как у всех."""
    import sys as _sys
    if _sys.platform != "win32":
        import pytest
        pytest.skip("DPAPI есть только на Windows")
    from chatter.security.crypto import encrypt_to_file, generate_entropy
    entropy = tmp_path / "entropy.bin"
    generate_entropy(entropy)
    monkeypatch.setenv("JARVIS_ENTROPY_FILE", str(entropy))
    enc = tmp_path / ".env.enc"
    encrypt_to_file(enc, b"TELEGRAM_BOT_TOKEN=tok-from-enc\n",
                    entropy_path=entropy)
    plain = tmp_path / ".env"
    plain.write_text("TELEGRAM_BOT_TOKEN=tok-from-plain\n", encoding="utf-8")
    monkeypatch.setattr(cw, "ENV_ENC_PATH", enc)
    monkeypatch.setattr(cw, "ENV_PATH", plain)
    assert cw._bot_token() == "tok-from-enc"


def test_bot_token_legacy_plaintext_when_no_enc(tmp_path, monkeypatch):
    """До cutover .enc нет — легаси-чтение plaintext живо (статус-кво)."""
    monkeypatch.setattr(cw, "ENV_ENC_PATH", tmp_path / ".env.enc")
    plain = tmp_path / ".env"
    plain.write_text('TELEGRAM_BOT_TOKEN="tok-plain"\n', encoding="utf-8")
    monkeypatch.setattr(cw, "ENV_PATH", plain)
    assert cw._bot_token() == "tok-plain"


def test_bot_token_broken_enc_falls_back_loudly(tmp_path, monkeypatch, capsys):
    """Standalone-инвариант алертера: битый .enc/пакет НЕ делает его немым —
    легаси plaintext доживает до шреда, а сбой печатается в лог гардиана
    (DEV-18: не молча)."""
    enc = tmp_path / ".env.enc"
    enc.write_bytes(b"JRVSEC1\x00garbage-not-a-real-blob")
    plain = tmp_path / ".env"
    plain.write_text("TELEGRAM_BOT_TOKEN=tok-plain\n", encoding="utf-8")
    monkeypatch.setattr(cw, "ENV_ENC_PATH", enc)
    monkeypatch.setattr(cw, "ENV_PATH", plain)
    assert cw._bot_token() == "tok-plain"
    assert "env.enc" in capsys.readouterr().out


def test_marker_roundtrip_preserves_alerted_flag(tmp_path, monkeypatch):
    # The `alerted` flag is the durable memory that pairs 🔴 with ✅. It MUST
    # survive a guardian restart (PID 8928 -> 12912 happened in prod), so it
    # lives in the marker file, not in the guardian's in-memory $lastState.
    monkeypatch.setattr(cw, "MARKER_PATH", tmp_path / "marker.json")
    cw._write_marker(1234.0, alerted=True)
    assert cw._read_marker() == {"last_alert_ts": 1234.0, "alerted": True}
    cw._write_marker(1234.0, alerted=False)
    assert cw._read_marker()["alerted"] is False


# ── мультиклиентность: алерт обязан называть КОГО чинить ───────────────────
#
# С одним клиентом «chatter-раннер лёг» было исчерпывающе. С N клиентами такой
# текст бесполезен: владелец не знает, кто именно упал. Маркеры тоже per-client
# — общий маркер означал бы, что авария одного клиента глушит алерт о другом
# на час кулдауна.

def test_paths_for_client_are_per_slug():
    assert cw.heartbeat_path_for("volska").name == "chatter_heartbeat_volska.txt"
    assert cw.marker_path_for("volska").name == "chatter_watch_alert_volska.json"


def test_paths_without_client_stay_legacy():
    """Ручной запуск без --client ведёт себя как раньше."""
    assert cw.heartbeat_path_for(None) == cw.HEARTBEAT_PATH
    assert cw.marker_path_for(None) == cw.MARKER_PATH


def test_alert_text_names_the_client():
    down = cw.alert_text("volska")
    up = cw.recovery_text("volska")
    assert "volska" in down, "владелец не поймёт, какого клиента чинить"
    assert "volska" in up
    assert "🔴" in down and "✅" in up


def test_alert_text_names_the_per_client_log():
    """Отсылка к logs/chatter_telethon.log увела бы владельца не в тот файл:
    у каждого клиента свой лог."""
    assert "chatter_volska.log" in cw.alert_text("volska")
    assert "chatter_volska.log" in cw.recovery_text("volska")


def test_alert_text_without_client_is_still_valid():
    assert "🔴" in cw.alert_text(None)
    assert "✅" in cw.recovery_text(None)


def test_markers_of_two_clients_do_not_collide():
    """Общий маркер = авария одного клиента глушит алерт о другом на кулдаун."""
    assert cw.marker_path_for("volska") != cw.marker_path_for("acme")
