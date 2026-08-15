# -*- coding: utf-8 -*-
"""Суточная проверка здоровья кэша (scripts/chatter_cache_digest.py).

Раз в сутки печатает hit-rate РАЗДЕЛЬНО по brain/classifier с базой и шумит
владельцу, только когда сработала сигнатура регрессии. Сеть замокана: под
pytest ни один реальный TG-запрос не уходит (тот же guard, что у
morning_digest/state_backup).
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
import time
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "chatter_cache_digest.py"
HOUR = 3600.0


def _load():
    spec = importlib.util.spec_from_file_location("chatter_cache_digest_script", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["chatter_cache_digest_script"] = mod
    spec.loader.exec_module(mod)
    return mod


def _mkdb(path: Path, rows) -> str:
    """rows = [(ts, tag, cache_read, cache_creation)]"""
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE llm_usage (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, "
        "tag TEXT, model TEXT, input_tokens INT, output_tokens INT, "
        "cache_read_input_tokens INT, cache_creation_input_tokens INT, "
        "cache_creation_5m INT, cache_creation_1h INT)")
    conn.executemany(
        "INSERT INTO llm_usage (ts, tag, model, input_tokens, output_tokens, "
        "cache_read_input_tokens, cache_creation_input_tokens, cache_creation_5m, "
        "cache_creation_1h) VALUES (?,?,'m',1900,200,?,?,0,?)",
        [(ts, tag, cr, cw, cw) for ts, tag, cr, cw in rows])
    conn.commit()
    conn.close()
    return str(path)


def test_report_splits_hit_rate_by_tag_with_base(tmp_path):
    now = time.time()
    db = _mkdb(tmp_path / "d.db", [
        (now - 600, "brain", 8801, 0),
        (now - 500, "brain", 8801, 0),
        (now - 400, "classifier", 0, 7770),
        (now - 300, "classifier", 0, 7770),
    ])
    mod = _load()
    rep = mod.build_report(db, now=now)
    assert rep["brain"] == (2, 2)
    assert rep["classifier"] == (0, 2)
    text = mod.format_report(rep)
    assert "brain" in text and "2/2" in text
    assert "classifier" in text and "0/2" in text


def test_small_base_is_visible_in_the_text(tmp_path):
    """«100%» на одном вызове не должно читаться как «всё хорошо»."""
    now = time.time()
    db = _mkdb(tmp_path / "d.db", [(now - 100, "classifier", 7770, 0)])
    mod = _load()
    text = mod.format_report(mod.build_report(db, now=now))
    assert "1/1" in text


def test_regression_signature_raises_the_alert_flag(tmp_path):
    now = time.time()
    db = _mkdb(tmp_path / "d.db", [
        (now - 1800, "classifier", 0, 7770),
        (now - 1200, "classifier", 0, 7790),
        (now - 600, "classifier", 0, 7760),
    ])
    mod = _load()
    rep = mod.build_report(db, now=now)
    assert rep["alert"] is True
    assert "🔴" in mod.format_report(rep)


def test_healthy_cache_does_not_alert(tmp_path):
    now = time.time()
    db = _mkdb(tmp_path / "d.db", [
        (now - 1800, "classifier", 0, 7770),
        (now - 1200, "classifier", 7770, 0),
        (now - 600, "classifier", 7770, 0),
    ])
    mod = _load()
    assert mod.build_report(db, now=now)["alert"] is False


def test_slow_dialog_does_not_alert(tmp_path):
    """Промахи через 3 часа — истёкший TTL, а не сломанный префикс."""
    now = time.time()
    db = _mkdb(tmp_path / "d.db", [
        (now - 9 * HOUR, "classifier", 0, 7770),
        (now - 6 * HOUR, "classifier", 0, 7770),
        (now - 3 * HOUR, "classifier", 0, 7770),
    ])
    mod = _load()
    assert mod.build_report(db, now=now)["alert"] is False


def test_config_reload_suppresses_the_alert(tmp_path, monkeypatch):
    """Три правки плейбука подряд = три законных промаха, не сигнатура."""
    now = time.time()
    db = _mkdb(tmp_path / "d.db", [
        (now - 1800, "classifier", 0, 7770),
        (now - 1200, "classifier", 0, 7790),
        (now - 600, "classifier", 0, 7760),
    ])
    mod = _load()
    monkeypatch.setattr(mod, "config_change_times",
                        lambda *a, **k: [now - 1900, now - 1300, now - 700])
    assert mod.build_report(db, now=now)["alert"] is False


def test_window_is_last_24h(tmp_path):
    now = time.time()
    db = _mkdb(tmp_path / "d.db", [
        (now - 30 * HOUR, "classifier", 0, 7770),   # позавчерашний — вне окна
        (now - 600, "classifier", 7770, 0),
    ])
    mod = _load()
    assert mod.build_report(db, now=now)["classifier"] == (1, 1)


# ── P16(г): видимость ростера — «by design» не должно выглядеть как авария ──


def _fake_root(tmp_path, clients=("demo", "demo2"), semidemo=False):
    (tmp_path / "chatter" / "clients").mkdir(parents=True)
    (tmp_path / "chatter" / "clients" / "active.yaml").write_text(
        "clients:\n" + "".join(f"  - {c}\n" for c in clients), encoding="utf-8")
    (tmp_path / "state").mkdir()
    if semidemo:
        (tmp_path / "state" / "chatter_semidemo_volska.flag").write_text("", encoding="utf-8")
    return tmp_path


def test_roster_reports_declared_clients_when_no_override(tmp_path):
    mod = _load()
    st = mod.roster_status(_fake_root(tmp_path))
    assert st["declared"] == ["demo", "demo2"]
    assert st["served"] == ["demo", "demo2"]
    assert st["override"] is None and st["muted"] == []


def test_semidemo_flag_is_reported_as_explicit_override(tmp_path):
    """Форензика 25.07: раннер обслуживал ТОЛЬКО volska с 22.07, а declared
    остался demo,demo2 — и это состояние не было видно нигде."""
    mod = _load()
    st = mod.roster_status(_fake_root(tmp_path, semidemo=True))
    assert st["override"] == "volska"
    assert st["served"] == ["volska"]
    assert st["muted"] == ["demo", "demo2"]


def test_muted_clients_named_in_the_report_text(tmp_path):
    mod = _load()
    line = mod.format_roster(mod.roster_status(_fake_root(tmp_path, semidemo=True)))
    assert "volska" in line
    assert "demo" in line and "demo2" in line
    assert "флаг" in line.lower()


def test_healthy_roster_line_is_quiet(tmp_path):
    """Без переопределения строка не должна кричать — иначе её перестанут читать."""
    mod = _load()
    line = mod.format_roster(mod.roster_status(_fake_root(tmp_path)))
    assert "⚠️" not in line and "🔴" not in line
    assert "demo" in line


def test_report_carries_roster(tmp_path):
    mod = _load()
    root = _fake_root(tmp_path, semidemo=True)
    db = _mkdb(tmp_path / "d.db", [(time.time() - 100, "classifier", 7770, 0)])
    rep = mod.build_report(db, now=time.time(), client_dir=root / "chatter" / "clients" / "volska",
                           root=root)
    assert rep["roster"]["override"] == "volska"
    assert "demo" in mod.format_report(rep)


def test_missing_active_yaml_does_not_crash_the_digest(tmp_path):
    """Раннер стартует с legacy-дефолтом — сводка обязана это пережить."""
    mod = _load()
    (tmp_path / "state").mkdir()
    st = mod.roster_status(tmp_path)
    assert st["declared"]          # legacy-дефолт, а не исключение


def test_no_network_send_under_pytest(tmp_path, monkeypatch):
    mod = _load()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "faketoken")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "42")
    monkeypatch.delenv("JARVIS_ALLOW_TELEGRAM_SEND", raising=False)

    def boom(*a, **k):
        raise AssertionError("network send attempted under test isolation")

    monkeypatch.setattr(mod.urllib.request, "urlopen", boom)
    assert mod.send_telegram("текст") is False


def test_missing_db_is_not_a_crash(tmp_path):
    """Клиент мог не завестись — суточная проверка не должна падать таском."""
    mod = _load()
    rep = mod.build_report(str(tmp_path / "нет-такой.db"), now=time.time())
    assert rep["alert"] is False and rep["classifier"] == (0, 0)
