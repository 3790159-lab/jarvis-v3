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

#: Вердикты владельца при разборе теневого набора. Ничего третьего: «наверное
#: полезно» — это неоценённая карточка, а не третий вердикт.
VERDICTS = ("useful", "junk")

#: Гейт теневой недели из спеки: доля мусора выше — детекторы не готовы.
NOISE_GATE = 0.20

#: СРОК ЖИЗНИ вердикта `junk`, а не вечность. Наблюдение опознаётся по хешу
#: фактов, а он ОДИНАКОВ у ложной тревоги и у настоящей аварии: у карточки
#: «сервис мёртв» нет поля «на этот раз по-настоящему». Вечное подавление
#: означало бы, что одна ошибка разбора делает нас слепыми к реальному падению
#: НАВСЕГДА. Поэтому мусор молчит N дней, а потом обязан всплыть снова.
JUNK_TTL_SEC = 7 * 86_400.0

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
           state: str = SHADOW, junk_ttl_sec: float = JUNK_TTL_SEC) -> str:
    """Записывает наблюдение. Возвращает `inserted`, `duplicate` или
    `suppressed`.

    Дубликатом считается уже открытое предложение с тем же
    `(kind, subject, evidence_hash)`. `suppressed` — то же наблюдение признано
    МУСОРОМ и срок подавления ЕЩЁ НЕ ИСТЁК: без подавления владелец разбирал бы
    одну и ту же ложную карточку каждый прогон, а показатель шума считал бы её
    заново. Срок конечен (`junk_ttl_sec`) — вечное подавление ослепило бы нас к
    настоящей аварии, у которой хеш фактов тот же.

    Вердикт `useful` подавлением не является: закрытая по делу дыра, если
    открылась снова, обязана снова быть видна.
    """
    _validate(proposal, state)
    digest = evidence_hash(proposal.evidence)
    judged_at = _latest_junk_verdict_at(conn, proposal, digest)
    if judged_at is not None and float(now) - judged_at < float(junk_ttl_sec):
        return "suppressed"
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


def _latest_junk_verdict_at(conn: sqlite3.Connection, proposal: Proposal,
                            digest: str) -> float | None:
    """Момент ПОСЛЕДНЕГО вердикта `junk` по этому наблюдению.

    Берётся ПОСЛЕДНИЙ вердикт: признал мусором снова — окно считается заново,
    иначе повторно отвергнутый шум полез бы обратно через неделю от первого раза.
    """
    row = conn.execute(
        "SELECT MAX(resolved_at) FROM proposals WHERE kind = ? AND subject = ?"
        " AND evidence_hash = ? AND result = 'junk' AND resolved_at IS NOT NULL",
        (proposal.kind, proposal.subject, digest)).fetchone()
    return None if row is None or row[0] is None else float(row[0])


def judge(conn: sqlite3.Connection, proposal_id: str, verdict: str, *,
          now: float, note: str = "") -> str:
    """Вердикт владельца по карточке: `useful` или `junk`.

    Непонятный вердикт и неизвестная карточка — явные ошибки (DEV-18), а не
    тихо проглоченный вызов: показатель шума, посчитанный по молча потерянным
    вердиктам, хуже отсутствующего.
    """
    if verdict not in VERDICTS:
        raise ProposalError(
            f"вердикт должен быть одним из {VERDICTS}, получено {verdict!r}")
    cursor = conn.execute(
        "UPDATE proposals SET resolved_at = ?, result = ?,"
        " snooze_until = NULL WHERE id = ? AND resolved_at IS NULL",
        (float(now), verdict, proposal_id))
    if cursor.rowcount != 1:
        raise ProposalError(
            f"карточка {proposal_id!r} не найдена среди незакрытых")
    if note:
        conn.execute("UPDATE proposals SET proposed_action = json_set("
                     "proposed_action, '$.judge_note', ?) WHERE id = ?",
                     (note, proposal_id))
    conn.commit()
    return verdict


def noise_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    """Показатель шума: доля мусора среди ОЦЕНЁННЫХ карточек.

    Неоценённая карточка не доказана ни полезной, ни мусорной — в знаменатель
    она не идёт, но показывается отдельно.

    При нуле оценённых показатель НЕ ОПРЕДЕЛЁН (`None`), а не 0.0: «мусора 0
    из 0» читается как «шума нет», и это самый дешёвый способ соврать себе.
    """
    judged = conn.execute(
        "SELECT COUNT(*) FROM proposals WHERE result IN (?, ?)",
        VERDICTS).fetchone()[0]
    junk = conn.execute(
        "SELECT COUNT(*) FROM proposals WHERE result = 'junk'").fetchone()[0]
    unjudged = conn.execute(
        "SELECT COUNT(*) FROM proposals WHERE result IS NULL").fetchone()[0]
    ratio = None if judged == 0 else junk / judged
    return {
        "judged": judged,
        "junk": junk,
        "unjudged": unjudged,
        "ratio": ratio,
        "gate": NOISE_GATE,
        "gate_pass": None if ratio is None else ratio <= NOISE_GATE,
    }


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
