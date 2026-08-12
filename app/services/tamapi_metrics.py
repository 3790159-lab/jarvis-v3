"""Метрики клиентского дашборда TAMAPI (спека docs/dashboard/CLIENT_SCREENS.md).

РЕШЕНИЕ: модуль открывает СВОЁ соединение в режиме read-only
(`file:...?mode=ro`), а не переиспользует `Store`. Почему так: дашборд — это
читатель чужой боевой БД с перепиской живых людей, и «не может писать» здесь
должно держаться механикой SQLite, а не дисциплиной вызывающего. Побочная
выгода — дашборд не конкурирует за writer-lock с раннером.

Всё, что тут считается, считается ИЗ ФАКТОВ в БД. Если данных за период нет,
метрика возвращает `None`, а не 0: ноль на графике читается как «было и упало»,
пустая метрика — как «ещё не накопилось». Это разные вещи, и врать формой мы не
имеем права.
"""
from __future__ import annotations

import sqlite3
import statistics
from dataclasses import dataclass, field
from pathlib import Path

# Периоды: диапазон И шаг агрегации меняются вместе — 30 дней по часам это
# 720 точек на телефоне, то есть шум вместо графика (спека §6.3).
PERIODS = {
    "day":   {"label": "День",    "span": 86400.0,       "bucket": 3600.0},
    "week":  {"label": "Тиждень", "span": 7 * 86400.0,   "bucket": 86400.0},
    "month": {"label": "Місяць",  "span": 30 * 86400.0,  "bucket": 86400.0},
}


@dataclass(frozen=True)
class MetricDef:
    key: str
    label: str
    unit: str = ""
    money: bool = False          # → правая ось (§6.2)
    needs: str = ""              # какая таблица нужна: "" | "funnel" | "payments"


METRICS: tuple[MetricDef, ...] = (
    MetricDef("dialogs",   "Діалоги"),
    MetricDef("length",    "Довжина", unit="повідомл."),
    MetricDef("qualified", "Кваліфіковано", needs="funnel"),
    MetricDef("handed",    "Передано"),
    MetricDef("payments",  "Оплати", needs="payments"),
    MetricDef("avg_check", "Середній чек", unit="$", money=True, needs="payments"),
    MetricDef("duration",  "Тривалість ведення", unit="дн"),
)
METRIC_BY_KEY = {m.key: m for m in METRICS}


@dataclass
class Series:
    key: str
    label: str
    unit: str
    money: bool
    points: list[tuple[float, float | None]] = field(default_factory=list)
    total: float | None = None
    prev_total: float | None = None
    history_since: float | None = None   # с какого момента данные вообще есть
    available: bool = True               # False → «історія накопичується з …»


def _peer(contact_id: str, display_name) -> str:
    """Как звать лида на экране. Имя, если раннер его запомнил, иначе id.

    Голый id — признак того, что о человеке НЕ известно ничего, а не нормальный
    вид карточки: на живом дриле оператор не смог возобновить диалог, увидев
    одно число. Fallback при этом остаётся — пустоту показывать нельзя."""
    return (display_name or "").strip() or contact_id.split(":", 1)[0]


def _ro(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _min_ts(conn: sqlite3.Connection, table: str) -> float | None:
    if not _table_exists(conn, table):
        return None
    row = conn.execute(f"SELECT MIN(ts) AS t FROM {table}").fetchone()
    return row["t"] if row and row["t"] is not None else None


# --------------------------------------------------------------- сырые выборки

def _dialogs(conn, a: float, b: float) -> list[str]:
    """Контакты с ≥1 ВХОДЯЩИМ за период — диалог считается по активности лида,
    а не по строке в contacts (иначе заведённый и молчащий контакт — «диалог»)."""
    rows = conn.execute(
        "SELECT DISTINCT contact_id FROM messages "
        "WHERE role='user' AND ts >= ? AND ts < ?", (a, b)).fetchall()
    return [r["contact_id"] for r in rows]


def _avg_length(conn, a: float, b: float) -> float | None:
    rows = conn.execute(
        "SELECT contact_id, COUNT(*) AS n FROM messages "
        "WHERE ts >= ? AND ts < ? GROUP BY contact_id", (a, b)).fetchall()
    return round(statistics.mean([r["n"] for r in rows]), 1) if rows else None


def _qualified(conn, a: float, b: float) -> float | None:
    if not _table_exists(conn, "funnel_transitions"):
        return None
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM funnel_transitions "
        "WHERE to_state='hot' AND ts >= ? AND ts < ?", (a, b)).fetchone()
    return float(row["n"])


def _handed(conn, a: float, b: float) -> float | None:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM console_cards "
        "WHERE kind='escalation' AND ts >= ? AND ts < ?", (a, b)).fetchone()
    return float(row["n"])


def _payments(conn, a: float, b: float) -> float | None:
    if not _table_exists(conn, "payments"):
        return None
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM payments WHERE ts >= ? AND ts < ?", (a, b)).fetchone()
    return float(row["n"])


def _avg_check(conn, a: float, b: float) -> float | None:
    """Только по строкам С суммой — «Без суми» в среднее не входит и в UI
    подписывается «за N з M оплат»."""
    if not _table_exists(conn, "payments"):
        return None
    row = conn.execute(
        # Минорные целые (арка payments): в мажорные переводим только здесь,
        # на границе показа, и результат никуда не сохраняем.
        "SELECT AVG(amount_minor) AS a FROM payments "
        "WHERE amount_minor IS NOT NULL AND ts >= ? AND ts < ?", (a, b)).fetchone()
    return round(row["a"] / 100.0, 1) if row and row["a"] is not None else None


def _duration_days(conn, a: float, b: float) -> float | None:
    """МЕДИАНА, не среднее: один заброшенный диалог, висящий месяц, утаскивает
    среднее и делает метрику бесполезной (спека §6.4)."""
    rows = conn.execute(
        "SELECT contact_id, MIN(ts) AS f, MAX(ts) AS l FROM messages "
        "GROUP BY contact_id HAVING MAX(ts) >= ? AND MAX(ts) < ?", (a, b)).fetchall()
    spans = [(r["l"] - r["f"]) / 86400.0 for r in rows if r["l"] > r["f"]]
    return round(statistics.median(spans), 1) if spans else None


_CALC = {
    "dialogs":   lambda c, a, b: float(len(_dialogs(c, a, b))),
    "length":    _avg_length,
    "qualified": _qualified,
    "handed":    _handed,
    "payments":  _payments,
    "avg_check": _avg_check,
    "duration":  _duration_days,
}


# ------------------------------------------------------------------ публичное

def series_for(db_path, keys: list[str], period: str, *, now: float) -> list[Series]:
    """Ряды для выбранных метрик за период. Точки — по бакетам периода."""
    cfg = PERIODS.get(period) or PERIODS["week"]
    span, bucket = cfg["span"], cfg["bucket"]
    start = now - span
    out: list[Series] = []
    with _ro(db_path) as conn:
        first_funnel = _min_ts(conn, "funnel_transitions")
        first_pay = _min_ts(conn, "payments")
        for key in keys:
            d = METRIC_BY_KEY.get(key)
            if d is None:
                continue
            since = {"funnel": first_funnel, "payments": first_pay}.get(d.needs)
            s = Series(key=d.key, label=d.label, unit=d.unit, money=d.money,
                       history_since=since,
                       available=(d.needs == "" or since is not None))
            calc = _CALC[key]
            t = start
            while t < now:
                s.points.append((t, calc(conn, t, min(t + bucket, now))))
                t += bucket
            s.total = calc(conn, start, now)
            s.prev_total = calc(conn, start - span, start)
            out.append(s)
    return out


def summary(db_path, *, now: float, period: str = "week") -> dict:
    """Всё для главного экрана + плиток «Динамики» одним проходом."""
    cfg = PERIODS.get(period) or PERIODS["week"]
    start = now - cfg["span"]
    with _ro(db_path) as conn:
        dialogs = _dialogs(conn, start, now)
        qualified = _qualified(conn, start, now)
        handed = _handed(conn, start, now)
        pays = _payments(conn, start, now)
        paid_rows = (conn.execute(
            "SELECT amount_minor FROM payments WHERE ts >= ? AND ts < ?", (start, now)
        ).fetchall() if _table_exists(conn, "payments") else [])
        # Суммы хранятся в МИНОРНЫХ целых (арка payments): делим на 100 только
        # здесь, на границе показа, и никогда не храним результат.
        with_amount = [r["amount_minor"] / 100.0 for r in paid_rows
                       if r["amount_minor"] is not None]
        out_today = conn.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE role='assistant' AND ts >= ?",
            (now - now % 86400.0,)).fetchone()["n"]
        return {
            "funnel": {
                "dialogs": len(dialogs),
                "qualified": qualified,
                "handed": handed,
                "payments": pays,
            },
            "avg_check": (round(statistics.mean(with_amount), 1) if with_amount else None),
            "avg_check_basis": (len(with_amount), len(paid_rows)),
            "outbound_today": out_today,
            "history": {
                "funnel_since": _min_ts(conn, "funnel_transitions"),
                "payments_since": _min_ts(conn, "payments"),
            },
        }


def needs_attention(db_path, *, now: float, limit: int = 10) -> list[dict]:
    """Блок «Требует вас»: контакты с ОТКРЫТОЙ карточкой эскалации.

    Открытость определяется рабочим флагом `esc_active:<contact>` — тем же, по
    которому дедуплицируются карточки в пульте: решающее действие владельца его
    чистит. Так веб и TG показывают одно и то же множество."""
    with _ro(db_path) as conn:
        rows = conn.execute(
            "SELECT key, value FROM runtime_flags WHERE key LIKE 'esc_active:%'"
        ).fetchall()
        out = []
        for r in rows:
            if not (r["value"] or "").strip():
                continue                       # флаг снят → карточка закрыта
            contact_id = r["key"].split(":", 1)[1]
            last = conn.execute(
                "SELECT text, ts FROM messages WHERE contact_id=? AND role='user' "
                "ORDER BY id DESC LIMIT 1", (contact_id,)).fetchone()
            card = conn.execute(
                "SELECT ts FROM console_cards WHERE contact_id=? AND kind='escalation' "
                "ORDER BY msg_id DESC LIMIT 1", (contact_id,)).fetchone()
            who = conn.execute(
                "SELECT display_name FROM contacts WHERE contact_id=?",
                (contact_id,)).fetchone()
            out.append({
                "contact_id": contact_id,
                "peer": _peer(contact_id, who["display_name"] if who else None),
                "last_text": (last["text"] if last else ""),
                "last_ts": (last["ts"] if last else None),
                "card_ts": (card["ts"] if card else None),
            })
        out.sort(key=lambda x: x["card_ts"] or 0, reverse=True)
        return out[:limit]


# Состояния, из которых бот сам уже не выйдет.
TERMINAL_STATES = ("dead", "closed")


def active_dialogs(db_path, *, now: float) -> int:
    """Сколько диалогов бот ВЕДЁТ прямо сейчас — цена решения «зупинити всіх».

    Считаем только тех, кого пауза реально заденет. Не в счёт: терминальные
    состояния, поимённо снятые с бота (`paused`) и уже переданные человеку
    (активная эскалация) — там бот молчит и без паузы. Счётчик, который
    считает их, завышает цену решения, а завышенная цена — такая же ложь,
    как заниженная.
    """
    feed = dialog_feed(db_path, now=now, limit=100_000)
    return sum(
        1 for f in feed
        if not f["paused"]
        and not f["needs_you"]
        and (f["state"] or "") not in TERMINAL_STATES
    )


def dialog_feed(db_path, *, now: float, limit: int = 30, flt: str = "all") -> list[dict]:
    with _ro(db_path) as conn:
        paid_ids = set()
        if _table_exists(conn, "payments"):
            paid_ids = {r["contact_id"] for r in
                        conn.execute("SELECT DISTINCT contact_id FROM payments")}
        active = {r["key"].split(":", 1)[1] for r in conn.execute(
            "SELECT key, value FROM runtime_flags WHERE key LIKE 'esc_active:%'")
            if (r["value"] or "").strip()}
        rows = conn.execute(
            "SELECT c.contact_id, c.state, c.paused, "
            "  (SELECT text FROM messages m WHERE m.contact_id=c.contact_id "
            "    ORDER BY m.id DESC LIMIT 1) AS last_text, "
            "  c.display_name AS display_name, "
            "  (SELECT ts FROM messages m WHERE m.contact_id=c.contact_id "
            "    ORDER BY m.id DESC LIMIT 1) AS last_ts "
            "FROM contacts c").fetchall()
        feed = []
        for r in rows:
            cid = r["contact_id"]
            item = {"contact_id": cid, "peer": _peer(cid, r["display_name"]),
                    "state": r["state"], "paused": bool(r["paused"]),
                    "last_text": r["last_text"] or "", "last_ts": r["last_ts"],
                    "needs_you": cid in active, "paid": cid in paid_ids}
            if flt == "needs" and not item["needs_you"]:
                continue
            if flt == "qualified" and r["state"] not in ("hot", "escalated", "closed"):
                continue
            if flt == "paid" and not item["paid"]:
                continue
            feed.append(item)
        feed.sort(key=lambda x: x["last_ts"] or 0, reverse=True)
        return feed[:limit]
