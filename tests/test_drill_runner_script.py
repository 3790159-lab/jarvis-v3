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

import pytest

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
    from chatter.core.drill import parse_scenario
    mod = _load()
    sc = parse_scenario("name: x\ncontact: c\nsteps:\n  - say: \"раз\"\n"
                        "  - say: \"два\"\n  - say: \"три\"\n  - say: \"чотири\"\n")
    est = mod.estimate_cost(sc.steps)
    assert est > 0
    text = mod.format_estimate(sc.steps, est)
    assert "холодный" in text
    # Тёплая ставка держится, только пока жив кэш: пауза длиннее часа делает
    # ХОЛОДНЫМ каждый следующий ход, и смета перестаёт быть правдой.
    assert "часа" in text or "TTL" in text


def test_spend_is_measured_from_usage(tmp_path):
    mod = _load()
    db = _db(tmp_path / "d.db", usage=[(1001.0, "brain", 8801, 0, 0),
                                       (1002.0, "classifier", 7681, 0, 0)])
    spent = mod.measure_spend(db, windows=[(1000.0, 1010.0)])
    assert spent > 0


def test_report_names_every_check(tmp_path):
    from chatter.core.drill import CheckResult, StepOutcome
    mod = _load()
    text = mod.format_report("Д-10", [
        StepOutcome(say="привіт",
                    checks=(CheckResult("cache", True, "ожидали hit, факт hit"),)),
        StepOutcome(say="ще",
                    checks=(CheckResult("obligations", False, "brief: open, ждали delivered"),)),
    ], money=mod.Money(estimate=0.14, drill=0.14, window=0.14))
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


# ── деньги: считаем ТОЛЬКО ходы дрила ────────────────────────────────────────
# Первый прогон: смета $0.14 на 4 шага, факт $0.1191 за ОДИН выполненный шаг —
# ×3.4 к ставке. Разбор по строкам llm_usage: постороннего трафика в окне не
# было, врала не БД, а метод. Два дефекта: (1) окно замера — от старта до конца
# прогона, то есть 45 минут таймаутов, в которые мог лечь любой чужой ход;
# (2) ставка $0.0352 — ТЁПЛАЯ, а первый ход сценария по договору холодный
# (`cache: miss`) и платит запись обоих префиксов по 1h-ставке.


def test_spend_counts_only_the_drill_turns(tmp_path):
    """Чужой ход, легший в паузу между шагами, не должен попадать в счёт дрила:
    иначе дрил «дорожает» ровно настолько, насколько владелец отошёл."""
    mod = _load()
    db = _db(tmp_path / "d.db", usage=[(1010.0, "brain", 8801, 0, 0),
                                       (2000.0, "brain", 8801, 0, 0)])
    drill_only = mod.measure_spend(db, windows=[(1000.0, 1060.0)])
    whole_window = mod.measure_spend(db, windows=[(1000.0, 3000.0)])
    assert drill_only > 0
    assert whole_window > drill_only, "фикстура обязана содержать посторонний ход"
    assert abs(whole_window - 2 * drill_only) < 1e-9


def test_skipped_step_contributes_no_window(tmp_path):
    """Пропущенный шаг ходов не делал — его окно в счёт не идёт."""
    mod = _load()
    db = _db(tmp_path / "d.db", usage=[(1500.0, "brain", 8801, 0, 0)])
    assert mod.measure_spend(db, windows=[]) == 0.0


def test_estimate_prices_the_first_turn_as_cold(tmp_path):
    """Смета обязана закладывать холодный старт: первый ход сценария по
    договору `cache: miss` и платит запись обоих префиксов ($6/M за 1h)."""
    mod = _load()
    from chatter.core.drill import parse_scenario
    sc = parse_scenario("name: x\ncontact: c\nsteps:\n"
                        "  - say: \"раз\"\n    expect: {cache: miss}\n"
                        "  - say: \"два\"\n    expect: {cache: hit}\n")
    est = mod.estimate_cost(sc.steps)
    assert est == pytest.approx(mod.COST_TURN_COLD + mod.COST_TURN_WARM)
    assert est > 2 * mod.COST_TURN_WARM, "плоская тёплая ставка занижала смету в 3.4 раза"


def test_estimate_assumes_cold_when_the_scenario_is_silent_about_cache(tmp_path):
    """Молчание сценария про кэш — не повод занижать смету: деньги считаем по
    худшему случаю, иначе владелец узнаёт цену после списания."""
    mod = _load()
    from chatter.core.drill import parse_scenario
    sc = parse_scenario("name: x\ncontact: c\nsteps:\n  - say: \"раз\"\n  - say: \"два\"\n")
    assert mod.estimate_cost(sc.steps) == pytest.approx(
        mod.COST_TURN_COLD + mod.COST_TURN_WARM)


def test_report_puts_estimate_fact_and_foreign_traffic_side_by_side():
    """Расхождение сметы с фактом должно быть видно в отчёте, а не всплывать
    вопросом владельца на следующий день."""
    from chatter.core.drill import CheckResult, StepOutcome
    mod = _load()
    text = mod.format_report("Д-10", [StepOutcome(say="раз", checks=(
        CheckResult("cache", True, "ожидали miss, факт miss"),))],
        money=mod.Money(estimate=0.2247, drill=0.1191, window=0.1500))
    assert "0.2247" in text and "0.1191" in text
    assert "0.0309" in text, "посторонний трафик в окне обязан быть назван отдельно"


# ── оркестрация: план целиком до старта, прогресс — на диск ──────────────────
# Прогон 2026-07-25 стоял 45 минут в фоновой команде и не показывал НИЧЕГО:
# stdout фонового процесса буферизован, а суфлёр печатал шаг только в него.
# Реплики пришлось диктовать вручную. Класс лечится двумя свойствами: весь план
# известен ДО старта, а прогресс живёт в файле, а не в чьём-то терминале.


def _scenario_file(tmp_path):
    sc = tmp_path / "s.yaml"
    sc.write_text(
        "name: тест\ncontact: c\nsteps:\n"
        "  - say: \"перша репліка\"\n    expect: {cache: miss}\n"
        "  - say: \"друга репліка\"\n    expect: {card_delivered: true}\n",
        encoding="utf-8")
    return sc


def test_progress_file_exists_before_the_first_step_starts(tmp_path, capsys, monkeypatch):
    """К моменту, когда харнесс ждёт первую реплику, план уже на диске —
    иначе о ходе прогона можно узнать, только глядя в фоновый stdout."""
    mod = _load()
    db = _db(tmp_path / "d.db")
    out = tmp_path / "drills"
    seen = {}

    def _spy(*a, **kw):
        files = list(out.glob("*.md"))
        seen["text"] = files[0].read_text(encoding="utf-8") if files else None
        return False

    monkeypatch.setattr(mod, "step_signal_seen", _spy)
    mod.main([str(_scenario_file(tmp_path)), "--db", db, "--out", str(out),
              "--log", str(tmp_path / "no.log"), "--yes", "--step-timeout", "0.1"])
    assert seen["text"], "к первому ожиданию прогресс-файл обязан существовать"
    assert "перша репліка" in seen["text"] and "друга репліка" in seen["text"]


def test_plan_names_every_replica_and_the_owner_actions(tmp_path, capsys):
    """Владелец должен видеть ВЕСЬ сценарий до старта — включая шаги, где от
    него ждут не только реплику (тап по карточке)."""
    mod = _load()
    rc = mod.main([str(_scenario_file(tmp_path)), "--db", str(tmp_path / "n.db"),
                   "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "перша репліка" in out and "друга репліка" in out
    assert "карточк" in out.lower(), "шаг с карточкой обязан быть помечен в плане"


def test_timed_out_run_exits_nonzero_and_says_so_in_the_report(tmp_path, capsys):
    """Сигнала не было ни на одном шаге: прогон НЕ состоялся — ненулевой код и
    явный вердикт в отчёте, а не тихий зелёный ноль."""
    mod = _load()
    db = _db(tmp_path / "d.db")
    out = tmp_path / "drills"
    rc = mod.main([str(_scenario_file(tmp_path)), "--db", db, "--out", str(out),
                   "--log", str(tmp_path / "no.log"), "--yes", "--step-timeout", "0.1"])
    assert rc == 2, "пропуск шагов обязан давать ненулевой exit"
    report = sorted(out.glob("*.md"))[0].read_text(encoding="utf-8")
    assert "НЕ СОСТОЯЛСЯ" in report
    assert "НЕ СОСТОЯЛСЯ" in capsys.readouterr().out


def test_plan_warns_about_expectations_already_satisfied(tmp_path, capsys):
    """Дрил идёт по живому контакту: слот мог закрыться прошлым прогоном.
    Владелец должен увидеть пустую проверку ДО того, как заплатит за прогон."""
    mod = _load()
    db = _db(tmp_path / "d.db", obligations=[("c", "owner_write", "delivered")])
    sc = tmp_path / "s.yaml"
    sc.write_text("name: x\ncontact: c\nsteps:\n  - say: \"оплата\"\n"
                  "    expect: {obligations: {owner_write: delivered}}\n", encoding="utf-8")
    mod.main([str(sc), "--db", db, "--dry-run"])
    out = capsys.readouterr().out
    assert "owner_write" in out and "ничего не докажет" in out


def test_missing_db_does_not_hide_the_plan(tmp_path, capsys):
    """Нет БД — план всё равно печатается, а причина названа вслух: молчаливое
    проглатывание ошибки здесь скрыло бы, что снимок «до» не прочитан."""
    mod = _load()
    sc = tmp_path / "s.yaml"
    sc.write_text("name: x\ncontact: c\nsteps:\n  - say: \"раз\"\n", encoding="utf-8")
    rc = mod.main([str(sc), "--db", str(tmp_path / "нет.db"), "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0 and "раз" in out
    assert "снимок" in out.lower()
