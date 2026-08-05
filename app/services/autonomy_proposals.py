# -*- coding: utf-8 -*-
"""Ш1 яруса 2: хранилище ПРЕДЛОЖЕНИЙ, работающее только в тени.

Спека: `docs/superpowers/specs/2026-08-05-autonomy-tier2-proposal-cards.md`.

Что здесь есть: таблица `proposals`, канонический хеш наблюдения и запись с
идемпотентностью. Чего здесь НЕТ и не должно появиться на этом шаге: любой
транспорт наружу. Ш1 копит и молчит; отправка — это Ш2, отдельное решение
владельца. Сторож `test_module_cannot_reach_the_outside_world` держит границу
импортами, чтобы «случайно отправилось» не стало возможным физически.

Идемпотентность: `(kind, subject, evidence_hash)` уникальны среди НЕЗАКРЫТЫХ
(`resolved_at IS NULL`). Один и тот же дрейф не плодит карточки, а однажды
закрытая и снова открывшаяся дыра снова видна — иначе она стала бы навсегда
невидимой.

⚠️ Контракт детектора: в `evidence` кладутся только СТАБИЛЬНЫЕ факты. Любое
изменчивое поле (метка времени наблюдения, счётчик прогонов) меняет хеш и на
каждом прогоне рождает новое «наблюдение» — это шум, который упрётся в гейт
20%. Время наблюдения живёт в `detected_at`, не в `evidence`.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

DEFAULT_DB_PATH = Path("state") / "autonomy.db"

#: Полный жизненный цикл из спеки. На Ш1 РАЗРЕШЕНО писать только `shadow`.
VALID_STATES = ("shadow", "sent", "accepted", "declined", "snoozed",
                "expired", "executed", "failed")
SHADOW = "shadow"

#: Уровни автономности 0..4 (контракт safety-policy: дефолт fail-closed 4).
VALID_ACTION_LEVELS = (0, 1, 2, 3, 4)

SCHEMA = """
CREATE TABLE IF NOT EXISTS proposals (
    id              TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,
    subject         TEXT NOT NULL,
    detected_at     REAL NOT NULL,
    evidence        TEXT NOT NULL,
    evidence_hash   TEXT NOT NULL,
    action_level    INTEGER NOT NULL,
    proposed_action TEXT NOT NULL,
    state           TEXT NOT NULL DEFAULT 'shadow',
    snooze_until    REAL,
    resolved_at     REAL,
    result          TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS proposals_open_identity
    ON proposals (kind, subject, evidence_hash) WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS proposals_state ON proposals (state, detected_at);
"""


class ProposalError(ValueError):
    """Нарушен контракт предложения. Явно, не тихо (DEV-18)."""


@dataclass(frozen=True)
class Proposal:
    kind: str
    subject: str
    evidence: Mapping[str, Any]
    action_level: int
    proposed_action: Mapping[str, Any] = field(default_factory=dict)


def evidence_hash(evidence: Mapping[str, Any]) -> str:
    """Канонический хеш наблюдения: порядок ключей не влияет."""
    canonical = json.dumps(evidence, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def connect(path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Открывает БД и доводит схему. Каталог создаётся при необходимости —
    но только тот, что запрошен: на импорте модуль ничего не трогает."""
    target = Path(path)
    if target.parent and not target.parent.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(target))
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    connection.commit()
    return connection


def _validate(proposal: Proposal, state: str) -> None:
    if state not in VALID_STATES:
        raise ProposalError(f"неизвестное состояние: {state!r}")
    if state != SHADOW:
        raise ProposalError(
            f"на Ш1 разрешена только тень, запрошено {state!r} — "
            f"отправка наружу это Ш2 и отдельное решение владельца")
    level = proposal.action_level
    if isinstance(level, bool) or not isinstance(level, int) or level not in VALID_ACTION_LEVELS:
        raise ProposalError(f"action_level должен быть 0..4, получено {level!r}")
    for name in ("kind", "subject"):
        value = getattr(proposal, name)
        if not isinstance(value, str) or not value.strip():
            raise ProposalError(f"{name} обязан быть непустой строкой")
    if not isinstance(proposal.evidence, Mapping):
        raise ProposalError("evidence обязан быть словарём фактов")


def record(conn: sqlite3.Connection, proposal: Proposal, *, now: float,
           state: str = SHADOW) -> str:
    """Записывает наблюдение. Возвращает `inserted` или `duplicate`.

    Дубликатом считается уже открытое предложение с тем же
    `(kind, subject, evidence_hash)`.
    """
    _validate(proposal, state)
    digest = evidence_hash(proposal.evidence)
    try:
        conn.execute(
            "INSERT INTO proposals (id, kind, subject, detected_at, evidence,"
            " evidence_hash, action_level, proposed_action, state)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, proposal.kind, proposal.subject, float(now),
             json.dumps(proposal.evidence, ensure_ascii=False, sort_keys=True),
             digest, int(proposal.action_level),
             json.dumps(proposal.proposed_action, ensure_ascii=False, sort_keys=True),
             state))
    except sqlite3.IntegrityError:
        return "duplicate"
    conn.commit()
    return "inserted"


def list_shadow(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Теневой набор для обзора владельцем — в порядке обнаружения."""
    rows = conn.execute(
        "SELECT * FROM proposals WHERE state = 'shadow' AND resolved_at IS NULL"
        " ORDER BY detected_at, rowid").fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["evidence"] = json.loads(item["evidence"])
        item["proposed_action"] = json.loads(item["proposed_action"])
        out.append(item)
    return out
