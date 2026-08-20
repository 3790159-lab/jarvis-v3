# -*- coding: utf-8 -*-
"""Шапка отчёта дрила обязана называть, ЧЕЙ это прогон.

Отчёты копятся в `state/drills` на машине, где клиентов уже несколько, а имя
файла — это `<unix_ts>.md` и только оно. Отчёт, не называющий клиента, нельзя
отличить от чужого — а на нём основывают вывод «дрил прогнан, бот отвечает».
Проба подключения `chatter.connect.probes._names_client` доказывает
принадлежность ТЕЛОМ файла, и до этой правки тело её не удовлетворяло.

Второе свойство здесь не менее важно первого: у старых сценариев полей
`client`/`contact` может не быть вовсе. Шапка, подставившая туда «что-нибудь»
(имя сценария, пустую строку, `None`), хуже отсутствующей: она делает чужой
файл СВОИМ на вид и уводит от повторного платного прогона в ложный зелёный.

$0: ни сети, ни Telegram, ни живого бота — сигналов нет, прогон падает по
таймауту шага, и это ровно то, что нужно: отчёт пишется в любом исходе.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

from chatter.connect.probes import _DRILL_TITLE_RE, _names_client

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "drill_runner.py"

SLUG = "volska"
CONTACT = "237616472:volska"
# Слаг, которого НЕТ внутри contact_id: иначе «отчёт назвал клиента» нельзя
# отличить от «в отчёте случайно оказалась подстрока контакта».
OTHER_SLUG = "yarina"
OTHER_CONTACT = "8849893367:yarina"


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


def _scenario(tmp_path: Path, *, client=None, contact=CONTACT, name="Д-10") -> str:
    sc = tmp_path / "s.yaml"
    body = f"name: {name}\n"
    if contact is not None:
        body += f"contact: {contact}\n"
    if client is not None:
        body += f"client: {client}\n"
    body += "steps:\n  - say: \"перша репліка\"\n  - say: \"друга репліка\"\n"
    sc.write_text(body, encoding="utf-8")
    return str(sc)


def _run(tmp_path: Path, scenario: str, *extra) -> str:
    """Прогон без сигналов: вердикт будет «НЕ СОСТОЯЛСЯ», отчёт — на диске."""
    mod = _load()
    out = tmp_path / "drills"
    mod.main([scenario, "--db", _db(tmp_path / "d.db"), "--out", str(out),
              "--log", str(tmp_path / "no.log"), "--yes",
              "--step-timeout", "0.1", *extra])
    return sorted(out.glob("*.md"))[0].read_text(encoding="utf-8")


def _identity_lines(text: str) -> list[str]:
    """Строки шапки, говорящие о принадлежности прогона."""
    return [ln for ln in text.splitlines()[:8]
            if "лиент" in ln or "онтакт" in ln]


# ── свойство 1: прогон называет своего клиента и свой контакт ────────────────


def test_report_head_names_client_and_contact():
    mod = _load()
    from chatter.core.drill import CheckResult, StepOutcome
    text = mod.format_report(
        "Д-10", [StepOutcome(say="привіт",
                             checks=(CheckResult("cache", True, "hit"),))],
        money=mod.Money(estimate=0.14, drill=0.14, window=0.14),
        client=SLUG, contact=CONTACT)
    assert SLUG in text, "слаг клиента обязан быть в отчёте"
    assert CONTACT in text, "дрил-контакт обязан быть в отчёте"
    assert _identity_lines(text), "принадлежность обязана стоять в ШАПКЕ"


def test_report_satisfies_the_connect_probe_that_proves_ownership():
    """Настоящий потребитель, а не наша фантазия о нём: S13 признаёт отчёт
    своим только если клиент назван в теле отдельным словом."""
    mod = _load()
    from chatter.core.drill import StepOutcome
    text = mod.format_report(
        "Д-10", [StepOutcome(say="привіт")],
        money=mod.Money(estimate=0.14, drill=0.0, window=0.0),
        client=SLUG, contact=CONTACT)
    assert _names_client(text, SLUG, []) is True


def test_title_line_stays_readable_for_the_probe():
    """Шапку `# Дрил: <name>` читает `_DRILL_TITLE_RE` по ПЕРВОЙ строке файла.
    Дописать принадлежность в ту же строку значило бы утащить в `name` всё
    остальное — поэтому первая строка обязана остаться прежней."""
    mod = _load()
    from chatter.core.drill import StepOutcome
    text = mod.format_report(
        "Д-10", [StepOutcome(say="привіт")],
        money=mod.Money(estimate=0.1, drill=0.0, window=0.0),
        client=SLUG, contact=CONTACT)
    m = _DRILL_TITLE_RE.match(text.splitlines()[0])
    assert m is not None and m.group("name").strip() == "Д-10"


# ── свойство 2: нет полей — нет и выдуманных значений ────────────────────────


def test_report_without_fields_does_not_invent_an_owner():
    """Старый сценарий без `client`/`contact`. Отчёт не имеет права оказаться
    «чьим-то»: ни имя сценария, ни пустая строка, ни `None` клиентом не
    являются."""
    mod = _load()
    from chatter.core.drill import StepOutcome
    text = mod.format_report(
        "Д-10", [StepOutcome(say="привіт")],
        money=mod.Money(estimate=0.1, drill=0.0, window=0.0))
    assert "None" not in text
    assert not _names_client(text, SLUG, []), "безымянный прогон не наш"
    assert not _names_client(text, OTHER_SLUG, []), "и не чужой"
    for ln in _identity_lines(text):
        assert "не указан" in ln, (
            f"строка принадлежности обязана либо отсутствовать, либо честно "
            f"сказать «не указан», а не показывать пустоту: {ln!r}")
        assert "Д-10" not in ln, "имя сценария — не клиент"


# ── свойство 3: то же самое доезжает до ФАЙЛА на диске ───────────────────────


def test_runner_writes_owner_into_the_report_file(tmp_path):
    text = _run(tmp_path, _scenario(tmp_path, client=SLUG))
    assert SLUG in text and CONTACT in text
    assert _names_client(text, SLUG, []) is True


def test_report_names_the_contact_actually_judged_not_the_yaml_one(tmp_path):
    """`drill_nightly` ВСЕГДА передаёт `--contact` явно (06.08: сброс чистил
    один чат, судья читал другой из yaml). Отчёт обязан назвать тот контакт, по
    которому судили, иначе он документирует не тот прогон, что состоялся."""
    text = _run(tmp_path, _scenario(tmp_path, client=SLUG),
                "--contact", OTHER_CONTACT)
    assert OTHER_CONTACT in text
    assert CONTACT not in text, "yaml-контакт не судили — называть его нельзя"


def test_report_file_of_a_fieldless_scenario_claims_nobody(tmp_path):
    text = _run(tmp_path, _scenario(tmp_path, client=None, contact=None))
    assert not _names_client(text, SLUG, [])
    assert not _names_client(text, OTHER_SLUG, [])
