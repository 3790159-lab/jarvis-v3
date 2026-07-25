# -*- coding: utf-8 -*-
"""Живой шов дрил-харнесса (scripts/drill_runner.py): сбор фактов, ожидание
сигнала, смета, безопасность.

Сеть не трогаем: карточка-суфлёр и опрос сигнала мокаются. Главный
архитектурный инвариант — харнесс НЕ открывает getUpdates (второй long-poll на
токене контрол-бота = 409 Conflict и падение ЖИВОГО пульта владельца) —
держится тестом-сторожом по исходнику.
"""
from __future__ import annotations

import importlib.util
import inspect
import sqlite3
import sys
import time
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "drill_runner.py"


def _load():
    spec = importlib.util.spec_from_file_location("drill_runner_script", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["drill_runner_script"] = mod
    spec.loader.exec_module(mod)
    return mod


def _db(path: Path, *, msgs=(), usage=(), obligations=(), cards=(), events=()):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id TEXT, role TEXT, text TEXT, ts REAL);
        CREATE TABLE llm_usage (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL,
            tag TEXT, model TEXT, input_tokens INT, output_tokens INT,
            cache_read_input_tokens INT, cache_creation_input_tokens INT,
            cache_creation_5m INT, cache_creation_1h INT);
        CREATE TABLE contact_obligations (contact_id TEXT, okey TEXT, kind TEXT,
            owed_by TEXT, status TEXT, detail TEXT, created_msg_id INT,
            closed_msg_id INT, created_ts REAL, closed_ts REAL);
        CREATE TABLE console_cards (msg_id INT, contact_id TEXT, kind TEXT, ts REAL);
        CREATE TABLE control_events (id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT, contact_id TEXT, detail TEXT, ts REAL);
        CREATE TABLE contact_profile (contact_id TEXT, text TEXT, ts REAL);
    """)
    conn.executemany("INSERT INTO messages (contact_id, role, text, ts) VALUES (?,?,?,?)", msgs)
    conn.executemany("INSERT INTO llm_usage (ts, tag, model, input_tokens, output_tokens,"
                     " cache_read_input_tokens, cache_creation_input_tokens,"
                     " cache_creation_5m, cache_creation_1h)"
                     " VALUES (?,?,'m',100,50,?,?,0,?)", usage)
    conn.executemany("INSERT INTO contact_obligations (contact_id, okey, status)"
                     " VALUES (?,?,?)", obligations)
    conn.executemany("INSERT INTO console_cards (msg_id, contact_id, kind, ts)"
                     " VALUES (?,?,'escalation',?)", cards)
    conn.executemany("INSERT INTO control_events (kind, contact_id, ts) VALUES (?,?,?)", events)
    conn.commit()
    conn.close()
    return str(path)


# ── СТОРОЖ: никакого второго getUpdates ──────────────────────────────────────


def test_harness_never_polls_telegram():
    """409 Conflict на токене контрол-бота уронил бы ЖИВОЙ пульт владельца, а
    не только дрил. Тап принимает поллер раннера, харнесс читает БД."""
    src = _SCRIPT.read_text(encoding="utf-8")
    assert "getUpdates" not in src
    assert "ControlBotPoller" not in src


# ── сбор фактов (read-only) ──────────────────────────────────────────────────


def test_cache_hit_read_from_usage(tmp_path):
    mod = _load()
    t0 = 1000.0
    db = _db(tmp_path / "d.db", usage=[(t0 + 5, "classifier", 7681, 0, 0)])
    f = mod.collect_facts(db, contact="c", since_ts=t0, log_lines=[], before={})
    assert f.cache == "hit"


def test_cache_miss_read_from_usage(tmp_path):
    mod = _load()
    t0 = 1000.0
    db = _db(tmp_path / "d.db", usage=[(t0 + 5, "classifier", 0, 7681, 7681)])
    f = mod.collect_facts(db, contact="c", since_ts=t0, log_lines=[], before={})
    assert f.cache == "miss"


def test_cache_na_when_classifier_did_not_run(tmp_path):
    """Ход мог не дойти до классификатора — это «н/д», а не «промах»."""
    mod = _load()
    db = _db(tmp_path / "d.db", usage=[(1005.0, "brain", 8801, 0, 0)])
    f = mod.collect_facts(db, contact="c", since_ts=1000.0, log_lines=[], before={})
    assert f.cache == "n/a"


def test_usage_before_the_step_is_ignored(tmp_path):
    mod = _load()
    db = _db(tmp_path / "d.db", usage=[(900.0, "classifier", 7681, 0, 0)])
    f = mod.collect_facts(db, contact="c", since_ts=1000.0, log_lines=[], before={})
    assert f.cache == "n/a"


def test_obligations_and_profile_and_errors(tmp_path):
    mod = _load()
    db = _db(tmp_path / "d.db",
             obligations=[("c", "brief", "delivered"), ("c", "owner_write", "delivered")],
             events=[("classifier_error", None, 1005.0)],
             cards=[(93, "c", 1005.0)])
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO contact_profile (contact_id, text, ts) VALUES ('c','Чайна Гора',1005)")
    conn.commit(); conn.close()
    f = mod.collect_facts(db, contact="c", since_ts=1000.0, log_lines=[], before={})
    assert f.obligations == {"brief": "delivered", "owner_write": "delivered"}
    assert f.profile == "Чайна Гора"
    assert f.classifier_errors == 1
    assert f.cards_delivered == 1


def test_replies_and_process_ends_come_from_the_log(tmp_path):
    mod = _load()
    db = _db(tmp_path / "d.db")
    lines = [
        "2026-07-25 02:03:39 INFO ...: process START 237616472:volska batch=['x']",
        "2026-07-25 02:04:47 INFO ...telethon_tg: OUT InputPeerUser(...): привіт",
        "2026-07-25 02:04:50 INFO ...telethon_tg: OUT InputPeerUser(...): ще",
        "2026-07-25 02:04:51 INFO ...: process END 237616472:volska",
    ]
    f = mod.collect_facts(db, contact="c", since_ts=0.0, log_lines=lines, before={})
    assert f.replies == 2 and f.process_ends == 1


# ── ожидание сигнала: сообщение ИЛИ тап ──────────────────────────────────────


def test_signal_fires_on_new_lead_message(tmp_path):
    """Настоящий сигнал «ход случился» — входящее лида, а не тап кнопки."""
    mod = _load()
    db = _db(tmp_path / "d.db", msgs=[("c", "user", "привіт", 1001.0)])
    assert mod.step_signal_seen(db, contact="c", since_ts=1000.0, flag_key="k") is True


def test_signal_ignores_bot_messages(tmp_path):
    mod = _load()
    db = _db(tmp_path / "d.db", msgs=[("c", "assistant", "ответ", 1001.0)])
    assert mod.step_signal_seen(db, contact="c", since_ts=1000.0, flag_key="k") is False


def test_signal_fires_on_button_tap(tmp_path):
    """Кнопка — запасной путь, если сообщение не долетело."""
    mod = _load()
    db = _db(tmp_path / "d.db")
    conn = sqlite3.connect(db)
    conn.executescript("CREATE TABLE runtime_flags (key TEXT PRIMARY KEY, value TEXT, ts REAL)")
    conn.execute("INSERT INTO runtime_flags VALUES ('drill:step1','1',1001.0)")
    conn.commit(); conn.close()
    assert mod.step_signal_seen(db, contact="c", since_ts=1000.0,
                               flag_key="drill:step1") is True


def test_no_signal_yet(tmp_path):
    mod = _load()
    db = _db(tmp_path / "d.db")
    assert mod.step_signal_seen(db, contact="c", since_ts=1000.0, flag_key="k") is False


# ── деньги и безопасность ────────────────────────────────────────────────────


def test_cost_estimate_is_printed_before_the_run():
    """Молчаливый прогон на 40 шагов недопустим."""
    mod = _load()
    est = mod.estimate_cost(steps=4)
    assert est > 0
    assert "4" in mod.format_estimate(4, est)


def test_spend_is_measured_from_usage(tmp_path):
    mod = _load()
    db = _db(tmp_path / "d.db", usage=[(1001.0, "brain", 8801, 0, 0),
                                       (1002.0, "classifier", 7681, 0, 0)])
    spent = mod.measure_spend(db, since_ts=1000.0)
    assert spent > 0


def test_report_names_every_check(tmp_path):
    from chatter.core.drill import CheckResult
    mod = _load()
    text = mod.format_report("Д-10", [
        {"say": "привіт", "checks": [CheckResult("cache", True, "ожидали hit, факт hit")]},
        {"say": "ще", "checks": [CheckResult("obligations", False, "brief: open, ждали delivered")]},
    ], spent=0.14)
    assert "✅" in text and "🔴" in text
    assert "cache" in text and "obligations" in text
    assert "0.14" in text


def test_run_requires_explicit_consent(tmp_path, capsys):
    """Дрил стоит живых денег и требует действий человека. Запуск «просто
    посмотреть» не должен уходить в прогон и ждать сигналов 15 минут —
    поймано на себе при первой же обкатке."""
    mod = _load()
    sc = tmp_path / "s.yaml"
    sc.write_text("name: x\ncontact: c\nsteps:\n  - say: \"hi\"\n", encoding="utf-8")
    rc = mod.main([str(sc), "--db", str(tmp_path / "nodb.db")])
    out = capsys.readouterr().out
    assert rc == 0, "без --yes скрипт обязан выйти, а не начать прогон"
    assert "--yes" in out and "смета" in out


def test_dry_run_prints_the_plan(tmp_path, capsys):
    mod = _load()
    sc = tmp_path / "s.yaml"
    sc.write_text("name: x\ncontact: c\nsteps:\n  - say: \"первая\"\n"
                  "  - say: \"вторая\"\n", encoding="utf-8")
    rc = mod.main([str(sc), "--db", str(tmp_path / "nodb.db"), "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "первая" in out and "вторая" in out
