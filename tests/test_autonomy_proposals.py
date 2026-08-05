# -*- coding: utf-8 -*-
"""Ш1 яруса 2 — хранилище предложений (ТОЛЬКО тень).

Спека: `docs/superpowers/specs/2026-08-05-autonomy-tier2-proposal-cards.md`.
На шаге Ш1 система только НАКАПЛИВАЕТ предложения. Ничего не отправляется —
ни в Telegram, ни человеку. Тесты держат оба контракта: идемпотентность
(дрейф не плодит карточки) и невозможность выйти за пределы тени.
"""
from __future__ import annotations

import sqlite3

import pytest

from app.services import autonomy_proposals as ap


def _proposal(**over):
    base = dict(
        kind="register_script_without_task",
        subject="JarvisMorningDigest",
        evidence={"script": "scripts/register_morning_digest.ps1", "task_present": False},
        action_level=4,
        proposed_action={"action": "register_scheduled_task", "task": "JarvisMorningDigest"},
    )
    base.update(over)
    return ap.Proposal(**base)


@pytest.fixture()
def conn(tmp_path):
    connection = ap.connect(tmp_path / "autonomy.db")
    yield connection
    connection.close()


def test_schema_has_every_field_the_spec_names(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(proposals)")}
    assert {"id", "kind", "subject", "detected_at", "evidence", "evidence_hash",
            "action_level", "proposed_action", "state", "snooze_until",
            "resolved_at"} <= columns


def test_recording_stores_a_shadow_row(conn):
    outcome = ap.record(conn, _proposal(), now=1_000.0)
    assert outcome == "inserted"
    row = conn.execute("SELECT kind, subject, state, detected_at, action_level FROM proposals").fetchone()
    assert row["kind"] == "register_script_without_task"
    assert row["subject"] == "JarvisMorningDigest"
    assert row["state"] == "shadow"
    assert row["detected_at"] == 1_000.0
    assert row["action_level"] == 4


def test_same_observation_twice_does_not_duplicate(conn):
    assert ap.record(conn, _proposal(), now=1.0) == "inserted"
    assert ap.record(conn, _proposal(), now=2.0) == "duplicate"
    assert conn.execute("SELECT COUNT(*) FROM proposals").fetchone()[0] == 1


def test_evidence_hash_ignores_key_order(conn):
    first = _proposal(evidence={"a": 1, "b": 2})
    second = _proposal(evidence={"b": 2, "a": 1})
    assert ap.evidence_hash(first.evidence) == ap.evidence_hash(second.evidence)
    ap.record(conn, first, now=1.0)
    assert ap.record(conn, second, now=2.0) == "duplicate"


def test_changed_evidence_is_a_new_observation(conn):
    ap.record(conn, _proposal(), now=1.0)
    changed = _proposal(evidence={"script": "scripts/register_morning_digest.ps1",
                                 "task_present": False, "trigger_lost": True})
    assert ap.record(conn, changed, now=2.0) == "inserted"
    assert conn.execute("SELECT COUNT(*) FROM proposals").fetchone()[0] == 2


def test_resolved_row_does_not_block_a_fresh_detection(conn):
    """Идемпотентность держится среди НЕЗАКРЫТЫХ: иначе однажды закрытая
    дыра, открывшаяся снова, была бы навсегда невидимой."""
    ap.record(conn, _proposal(), now=1.0)
    conn.execute("UPDATE proposals SET state='expired', resolved_at=5.0")
    assert ap.record(conn, _proposal(), now=10.0) == "inserted"
    assert conn.execute("SELECT COUNT(*) FROM proposals").fetchone()[0] == 2


@pytest.mark.parametrize("state", ["sent", "accepted", "executed", "failed", "snoozed"])
def test_only_shadow_can_be_written_on_stage_one(conn, state):
    """Предохранитель против случайного открытия Ш2: запись любого другого
    состояния — явный отказ, а не тихая отправка."""
    with pytest.raises(ap.ProposalError):
        ap.record(conn, _proposal(), now=1.0, state=state)


def test_unknown_state_is_rejected(conn):
    with pytest.raises(ap.ProposalError):
        ap.record(conn, _proposal(), now=1.0, state="whatever")


def test_action_level_must_be_a_real_level(conn):
    with pytest.raises(ap.ProposalError):
        ap.record(conn, _proposal(action_level=9), now=1.0)
    with pytest.raises(ap.ProposalError):
        ap.record(conn, _proposal(action_level=True), now=1.0)


def test_module_cannot_reach_the_outside_world():
    """Архитектурный сторож «ноль отправки»: модуль тени не имеет права
    тянуть сетевой транспорт. Дешевле поймать импортом, чем поведением.

    Разбираем AST, а не текст: упоминание транспорта в комментарии — не
    выход наружу, и сторож не должен на нём краснеть.
    """
    import ast

    with open(ap.__file__, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    forbidden = {"urllib", "requests", "httpx", "socket", "smtplib", "ftplib",
                 "telegram", "http"}
    assert not (imported & forbidden), (
        f"модуль тени импортирует {imported & forbidden} — это выход наружу")


def test_default_database_lives_under_state(tmp_path):
    assert ap.DEFAULT_DB_PATH.parts[-2:] == ("state", "autonomy.db")


def test_connect_is_idempotent_on_an_existing_file(tmp_path):
    path = tmp_path / "autonomy.db"
    first = ap.connect(path)
    ap.record(first, _proposal(), now=1.0)
    first.close()
    second = ap.connect(path)
    assert second.execute("SELECT COUNT(*) FROM proposals").fetchone()[0] == 1
    second.close()


def test_shadow_rows_are_readable_for_the_owner_review(conn):
    ap.record(conn, _proposal(), now=1.0)
    ap.record(conn, _proposal(subject="JarvisErrorDigest"), now=2.0)
    rows = ap.list_shadow(conn)
    assert [row["subject"] for row in rows] == ["JarvisMorningDigest", "JarvisErrorDigest"]
    assert all(isinstance(row["evidence"], dict) for row in rows)
    assert all(isinstance(row["proposed_action"], dict) for row in rows)


def test_sqlite_enforces_the_idempotency_key(conn):
    """Уникальность живёт в БД, а не только в коде: обход через прямой INSERT
    обязан упереться в индекс."""
    ap.record(conn, _proposal(), now=1.0)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO proposals (id, kind, subject, detected_at, evidence,"
            " evidence_hash, action_level, proposed_action, state)"
            " VALUES ('x', ?, ?, 3.0, '{}', ?, 4, '{}', 'shadow')",
            ("register_script_without_task", "JarvisMorningDigest",
             ap.evidence_hash(_proposal().evidence)))
