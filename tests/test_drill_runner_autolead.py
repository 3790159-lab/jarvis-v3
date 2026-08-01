# -*- coding: utf-8 -*-
"""`--auto-lead`: харнесс сам шлёт реплики через процесс лида (стенд v2, Э3).

Что здесь проверяется — ровно то, из-за чего ночь 26.07 дала один дошедший
прогон из шести: реплика отправляется САМА, следующая уходит только после
ЗАКРЫТИЯ хода, а расхождение с текстом сценария в автоматическом режиме —
красное (стенд шлёт ровно сценарную строку, значит расхождение = баг стенда).

$0: ни сети, ни Telethon, ни живого процесса лида — лид инъектируется.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
import time
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "drill_runner.py"
_SCRIPTS = _SCRIPT.parent

DRILL = "237616472:volska"


def _load():
    spec = importlib.util.spec_from_file_location("drill_runner_script", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["drill_runner_script"] = mod
    spec.loader.exec_module(mod)
    return mod


def _db(path: Path) -> str:
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
    conn.commit()
    conn.close()
    return str(path)


def _scenario(tmp_path: Path, says, *, contact=DRILL) -> str:
    sc = tmp_path / "s.yaml"
    sc.write_text(f"name: авто\ncontact: {contact}\nsteps:\n"
                  + "".join(f"  - say: \"{s}\"\n" for s in says),
                  encoding="utf-8")
    return str(sc)


class _FakeLead:
    """Лид, который «отправляет» — то есть кладёт входящее в БД и дописывает
    закрытие хода в лог, как это сделал бы живой раннер."""

    def __init__(self, db: str, log: Path, *, contact=DRILL, echo=None,
                 close_turn=True):
        self.db, self.log, self.contact = db, log, contact
        self.said: list[str] = []
        self.closed = False
        self._echo = echo
        self._close_turn = close_turn

    def say(self, text: str) -> None:
        self.said.append(text)
        conn = sqlite3.connect(self.db)
        conn.execute("INSERT INTO messages (contact_id, role, text, ts) "
                     "VALUES (?,'user',?,?)",
                     (self.contact, self._echo or text, time.time()))
        conn.commit()
        conn.close()
        if self._close_turn:
            with self.log.open("a", encoding="utf-8") as fh:
                fh.write("12:00:00 volska: OUT привіт\n12:00:01 process END\n")

    def close(self) -> None:
        self.closed = True


def _run(mod, tmp_path, says, *, lead=None, extra=(), contact=DRILL):
    db = _db(tmp_path / "d.db")
    log = tmp_path / "run.log"
    log.write_text("", encoding="utf-8")
    out = tmp_path / "drills"
    lead = lead if lead is not None else _FakeLead(db, log, contact=contact)
    rc = mod.main([_scenario(tmp_path, says, contact=contact), "--db", db,
                   "--out", str(out), "--log", str(log), "--yes", "--auto-lead",
                   "--lead-peer", "777000", "--step-timeout", "3",
                   *extra],
                  lead_factory=lambda **kw: lead)
    report = sorted(out.glob("*.md"))
    return rc, lead, (report[0].read_text(encoding="utf-8") if report else "")


# ── ПРЕДОХРАНИТЕЛЬ: автоматика только на дрил-контакте ──────────────────────


def test_auto_lead_refuses_non_drill_contact(tmp_path):
    """Автолид САМ шлёт сообщения. Направить его на контакт живого клиента —
    это разговор с клиентом от имени стенда, необратимо."""
    mod = _load()
    rc, lead, _ = _run(mod, tmp_path, ["привіт"], contact="999999999:volska")
    assert rc == 2
    assert lead.said == [], "на чужом контакте лид не должен быть даже поднят"


def test_drill_contacts_come_from_the_single_source(tmp_path):
    """Третий список дрил-контактов = способ однажды разойтись с двумя
    существующими. Харнесс читает тот же, что и drill_reset."""
    mod = _load()
    import importlib.util as iu
    spec = iu.spec_from_file_location("reset_for_autolead",
                                      _SCRIPTS / "drill_reset.py")
    reset = iu.module_from_spec(spec)
    spec.loader.exec_module(reset)
    assert mod.drill_contacts() == reset.DRILL_CONTACTS


def test_auto_lead_requires_peer(tmp_path):
    mod = _load()
    db = _db(tmp_path / "d.db")
    rc = mod.main([_scenario(tmp_path, ["a"]), "--db", db, "--yes",
                   "--auto-lead", "--out", str(tmp_path / "o"),
                   "--log", str(tmp_path / "n.log")],
                  lead_factory=lambda **kw: None)
    assert rc == 2


# ── прогон целиком ───────────────────────────────────────────────────────────


def test_auto_lead_sends_every_step_and_closes_lead(tmp_path):
    mod = _load()
    rc, lead, report = _run(mod, tmp_path, ["крок 1", "крок 2"])
    assert lead.said == ["крок 1", "крок 2"]
    assert lead.closed is True
    assert rc == 0, report


def test_lead_is_closed_even_when_run_dies(tmp_path):
    """Процесс лида поднимается на прогон и гасится в finally — долгоживущего
    демона нет, гасить руками нечего."""
    mod = _load()
    db = _db(tmp_path / "d.db")
    log = tmp_path / "run.log"
    log.write_text("", encoding="utf-8")

    class _Boom(_FakeLead):
        def say(self, text):
            raise RuntimeError("лид умер")

    lead = _Boom(db, log)
    rc = mod.main([_scenario(tmp_path, ["a"]), "--db", db, "--yes",
                   "--auto-lead", "--lead-peer", "777000",
                   "--out", str(tmp_path / "o"), "--log", str(log),
                   "--step-timeout", "1"],
                  lead_factory=lambda **kw: lead)
    assert rc == 2
    assert lead.closed is True


# ── следующая реплика — только после ЗАКРЫТИЯ хода ──────────────────────────


def test_turn_closed_needs_both_end_and_out(tmp_path):
    """`process END` без единого OUT — это ход, который ничего не ответил.
    Считать его закрытым значит слать следующую реплику в молчащего бота."""
    mod = _load()
    assert mod.turn_closed(["x: OUT привіт", "process END"]) is True
    assert mod.turn_closed(["process END"]) is False
    assert mod.turn_closed(["x: OUT привіт"]) is False
    assert mod.turn_closed([]) is False


def test_step_without_turn_close_is_skipped_not_green(tmp_path):
    """Ход не закрылся — шаг не состоялся. Зелёный отчёт по молчанию бота
    хуже красного."""
    mod = _load()
    db = _db(tmp_path / "d.db")
    log = tmp_path / "run.log"
    log.write_text("", encoding="utf-8")
    lead = _FakeLead(db, log, close_turn=False)
    rc = mod.main([_scenario(tmp_path, ["a", "b"]), "--db", db, "--yes",
                   "--auto-lead", "--lead-peer", "777000",
                   "--out", str(tmp_path / "o"), "--log", str(log),
                   "--step-timeout", "1"],
                  lead_factory=lambda **kw: lead)
    assert rc == 2
    report = sorted((tmp_path / "o").glob("*.md"))[0].read_text(encoding="utf-8")
    assert "⛔" in report


def test_next_reply_goes_only_after_previous_turn_closed(tmp_path):
    """Две реплики за 46 с схлопнулись в один ход (26.07) — в v2 это инвариант
    кода: пока ход не закрыт, вторая реплика не уходит."""
    mod = _load()
    db = _db(tmp_path / "d.db")
    log = tmp_path / "run.log"
    log.write_text("", encoding="utf-8")
    order: list[str] = []

    class _Recording(_FakeLead):
        def say(self, text):
            order.append(f"say:{text}")
            super().say(text)
            order.append(f"turn-closed:{text}")

    lead = _Recording(db, log)
    rc = mod.main([_scenario(tmp_path, ["a", "b"]), "--db", db, "--yes",
                   "--auto-lead", "--lead-peer", "777000",
                   "--out", str(tmp_path / "o"), "--log", str(log),
                   "--step-timeout", "3"],
                  lead_factory=lambda **kw: lead)
    assert rc == 0
    assert order == ["say:a", "turn-closed:a", "say:b", "turn-closed:b"]


# ── расхождение текста в авторежиме = красное ───────────────────────────────


def test_text_mismatch_is_red_in_auto_mode(tmp_path):
    """В ручном режиме расхождение — предупреждение (человек мог опечататься).
    Стенд шлёт РОВНО сценарную строку, поэтому расхождение = баг стенда."""
    mod = _load()
    db = _db(tmp_path / "d.db")
    log = tmp_path / "run.log"
    log.write_text("", encoding="utf-8")
    lead = _FakeLead(db, log, echo="совсем другой текст")
    rc = mod.main([_scenario(tmp_path, ["крок 1"]), "--db", db, "--yes",
                   "--auto-lead", "--lead-peer", "777000",
                   "--out", str(tmp_path / "o"), "--log", str(log),
                   "--step-timeout", "3"],
                  lead_factory=lambda **kw: lead)
    assert rc == 1, "прогон состоялся, но стенд отправил не то — это красное"
    report = sorted((tmp_path / "o").glob("*.md"))[0].read_text(encoding="utf-8")
    assert "🔴" in report and "bench" in report


# ── таймеры ──────────────────────────────────────────────────────────────────


def test_auto_mode_does_not_wait_an_hour_for_the_first_step(tmp_path):
    """Часовой таймер первого шага существует ради человека, идущего к
    телефону. В авторежиме человека нет — час ожидания только маскирует
    поломку стенда."""
    mod = _load()
    db = _db(tmp_path / "d.db")
    log = tmp_path / "run.log"
    log.write_text("", encoding="utf-8")
    lead = _FakeLead(db, log, close_turn=False)
    started = time.time()
    rc = mod.main([_scenario(tmp_path, ["a"]), "--db", db, "--yes",
                   "--auto-lead", "--lead-peer", "777000",
                   "--out", str(tmp_path / "o"), "--log", str(log),
                   "--step-timeout", "1"],
                  lead_factory=lambda **kw: lead)
    assert rc == 2
    assert time.time() - started < 60


# ── инварианты ───────────────────────────────────────────────────────────────


def test_harness_still_does_not_import_telethon():
    """Лид — ОТДЕЛЬНЫЙ процесс. Импорт Telethon в судью означал бы, что боевая
    и тестовая сессии однажды окажутся в одном процессе (инвариант v2 §3)."""
    src = _SCRIPT.read_text(encoding="utf-8")
    assert "import telethon" not in src
    assert "from telethon" not in src
    assert "TelegramClient" not in src


def test_manual_mode_is_unchanged(tmp_path, monkeypatch):
    """Стенд — надстройка, а не замена: на живом клиенте автолида не будет
    никогда, ручной режим обязан работать как сегодня."""
    mod = _load()
    db = _db(tmp_path / "d.db")
    log = tmp_path / "run.log"
    log.write_text("", encoding="utf-8")
    polled = []

    def _spy(_db_, *, contact, since_ts, flag_key):
        polled.append(flag_key)
        return False

    monkeypatch.setattr(mod, "step_signal_seen", _spy)
    rc = mod.main([_scenario(tmp_path, ["a", "b"]), "--db", db, "--yes",
                   "--out", str(tmp_path / "o"), "--log", str(log),
                   "--step-timeout", "0.1"])
    assert rc == 2
    assert polled, "ручной режим по-прежнему опрашивает сигнал шага"


def test_no_funnel_gate_anywhere_in_the_bench():
    """Приёмка §9 п.5: ни одного обращения к funnel_gate в коде стенда."""
    for path in (_SCRIPT, _SCRIPTS / "drill_lead.py", _SCRIPTS / "drill_reset.py"):
        assert "funnel_gate" not in path.read_text(encoding="utf-8"), path
