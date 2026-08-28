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

from chatter.core.contact_ref import peer_label_of

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
    # Сколько наблюдений стоит за total/prev_total. «▼100%» при n=1 против n=1 —
    # это два разных лида, а не падение; процент без основания не сравнение.
    basis: int | None = None
    prev_basis: int | None = None
    history_since: float | None = None   # с какого момента данные вообще есть
    available: bool = True               # False → «історія накопичується з …»


def _peer(contact_id: str, display_name) -> str:
    """Как звать лида на экране. Имя, если раннер его запомнил, иначе id.

    Голый id — признак того, что о человеке НЕ известно ничего, а не нормальный
    вид карточки: на живом дриле оператор не смог возобновить диалог, увидев
    одно число. Fallback при этом остаётся — пустоту показывать нельзя."""
    return (display_name or "").strip() or peer_label_of(contact_id)


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


# Все ступени воронки считают ЛЮДЕЙ, а не строки. 12.08 один лид дал «4
# кваліфіковано» при «1 діалог» — 400%, потому что числитель считал СТРОКИ
# переходов (он четырежды выпадал из hot и возвращался), а знаменатель людей.
# Событийная правда не пропала: она живёт в блоке «Навантаження» (`load`).

def _hot_people(conn, a: float, b: float) -> set[str] | None:
    if not _table_exists(conn, "funnel_transitions"):
        return None
    return {r["contact_id"] for r in conn.execute(
        "SELECT DISTINCT contact_id FROM funnel_transitions "
        "WHERE to_state='hot' AND ts >= ? AND ts < ?", (a, b))}


def _carded_people(conn, a: float, b: float) -> set[str]:
    return {r["contact_id"] for r in conn.execute(
        "SELECT DISTINCT contact_id FROM console_cards "
        "WHERE kind='escalation' AND ts >= ? AND ts < ?", (a, b))}


def _paid_people(conn, a: float, b: float) -> set[str] | None:
    if not _table_exists(conn, "payments"):
        return None
    return {r["contact_id"] for r in conn.execute(
        "SELECT DISTINCT contact_id FROM payments WHERE ts >= ? AND ts < ?", (a, b))}


def _qualified(conn, a: float, b: float) -> float | None:
    people = _hot_people(conn, a, b)
    return None if people is None else float(len(people))


def _handed(conn, a: float, b: float) -> float | None:
    return float(len(_carded_people(conn, a, b)))


def _payments(conn, a: float, b: float) -> float | None:
    people = _paid_people(conn, a, b)
    return None if people is None else float(len(people))


def _events(conn, a: float, b: float) -> dict:
    """Нагрузка: СОБЫТИЯ за период. Отдельно от воронки и с явным именем —
    «сколько раз это случилось», а не «со сколькими людьми»."""
    cards = conn.execute(
        "SELECT COUNT(*) AS n FROM console_cards "
        "WHERE kind='escalation' AND ts >= ? AND ts < ?", (a, b)).fetchone()["n"]
    trans = None
    if _table_exists(conn, "funnel_transitions"):
        trans = conn.execute(
            "SELECT COUNT(*) AS n FROM funnel_transitions "
            "WHERE to_state='hot' AND ts >= ? AND ts < ?", (a, b)).fetchone()["n"]
    return {"transitions": trans, "cards": cards}


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


def _duration_spans(conn, a: float, b: float) -> list[float]:
    rows = conn.execute(
        "SELECT contact_id, MIN(ts) AS f, MAX(ts) AS l FROM messages "
        "GROUP BY contact_id HAVING MAX(ts) >= ? AND MAX(ts) < ?", (a, b)).fetchall()
    return [(r["l"] - r["f"]) / 86400.0 for r in rows if r["l"] > r["f"]]


def _duration_days(conn, a: float, b: float) -> float | None:
    """МЕДИАНА, не среднее: один заброшенный диалог, висящий месяц, утаскивает
    среднее и делает метрику бесполезной (спека §6.4).

    Результат НЕ округляется: округление до десятых ДНЯ схлопывало всё короче
    2.4 часа в ноль, и на экране стояло «0 хв» при диалоге на десять минут.
    Единицу выбирает показ (`_fmt_value`), а не хранение."""
    spans = _duration_spans(conn, a, b)
    return statistics.median(spans) if spans else None


_CALC = {
    "dialogs":   lambda c, a, b: float(len(_dialogs(c, a, b))),
    "length":    _avg_length,
    "qualified": _qualified,
    "handed":    _handed,
    "payments":  _payments,
    "avg_check": _avg_check,
    "duration":  _duration_days,
}

# Основание выборки там, где показатель — статистика по нескольким диалогам, а
# не счёт. У счётчиков основание совпадает со значением и смысла не несёт.
_BASIS = {
    "duration": lambda c, a, b: len(_duration_spans(c, a, b)),
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
            basis = _BASIS.get(key)
            if basis is not None:
                s.basis = basis(conn, start, now)
                s.prev_basis = basis(conn, start - span, start)
            out.append(s)
    return out


def summary(db_path, *, now: float, period: str = "week") -> dict:
    """Всё для главного экрана + плиток «Динамики» одним проходом.

    ВОРОНКА СЧИТАЕТ ЛЮДЕЙ И ТОЛЬКО ИЗ КОГОРТЫ. Когорта — первая ступень: те,
    кто писал в окне. Каждая следующая ступень — пересечение с ней, поэтому
    ступень физически не может быть больше когорты, а доля — больше 100%.
    Инвариант держится КОНСТРУКЦИЕЙ, а не зажимом `min(pct, 100)`: зажим прячет
    расхождение ровно там, где оно и означало ошибку счёта.

    Доля каждой ступени считается ОТ КОГОРТЫ, а не от предыдущей строки. Иначе
    «передан человеку, но классификатором не квалифицирован» даёт деление на
    ноль, а на живых данных 12.08 давало правдоподобные «75%», за которыми не
    стояло ничего.
    """
    cfg = PERIODS.get(period) or PERIODS["week"]
    start = now - cfg["span"]
    with _ro(db_path) as conn:
        dialogs = _dialogs(conn, start, now)
        cohort = set(dialogs)
        hot = _hot_people(conn, start, now)
        carded = _carded_people(conn, start, now)
        paid = _paid_people(conn, start, now)
        qualified = None if hot is None else float(len(cohort & hot))
        handed = float(len(cohort & carded))
        pays = None if paid is None else float(len(cohort & paid))
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
            "load": _events(conn, start, now),
            "avg_check": (round(statistics.mean(with_amount), 1) if with_amount else None),
            "avg_check_basis": (len(with_amount), len(paid_rows)),
            "outbound_today": out_today,
            "history": {
                "funnel_since": _min_ts(conn, "funnel_transitions"),
                "payments_since": _min_ts(conn, "payments"),
            },
        }


# Порог свежести карточки (решение владельца 12.08). Всё старше — «застаріле»:
# не удаляется и не гасится молча (тихо закрытый чужой долг — отдельный класс
# бага, P17), но и не лежит вперемешку со свежим.
STALE_AFTER = 48 * 3600.0


def needs_attention(db_path, *, now: float, limit: int = 50) -> list[dict]:
    """Блок «Требует вас»: контакты с ОТКРЫТОЙ карточкой эскалации.

    Открытость определяется рабочим флагом `esc_active:<contact>` — тем же, по
    которому дедуплицируются карточки в пульте: решающее действие владельца его
    чистит. Так веб и TG показывают одно и то же множество.

    Возрастов ДВА, и это разные вопросы: `card_ts` — когда бот поднял руку,
    `last_ts` — сколько человек ждёт ответа. Живьём 12.08 в блоке висели
    карточки 8- и 14-дневной давности вперемешку со свежей, причём одна — на
    контакте в терминальном состоянии: бот с ним уже не работает, а карточка
    открыта. Такое противоречие помечается, а не прячется."""
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
                "SELECT display_name, state FROM contacts WHERE contact_id=?",
                (contact_id,)).fetchone()
            card_ts = card["ts"] if card else None
            out.append({
                "contact_id": contact_id,
                "peer": _peer(contact_id, who["display_name"] if who else None),
                "last_text": (last["text"] if last else ""),
                "last_ts": (last["ts"] if last else None),
                "card_ts": card_ts,
                "stale": bool(card_ts is not None and now - card_ts > STALE_AFTER),
                "dead": bool(who is not None
                             and (who["state"] or "") in TERMINAL_STATES),
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


class OutgoingQueueMissing(RuntimeError):
    """Таблицы `outgoing_queue` в базе клиента НЕТ.

    Громкий тип, а не пустой ответ: отсутствие таблицы означает, что раннер
    этой базы ещё НЕ ПЕРЕЗАПУЩЕН на код с очередью, — то есть всё, что владелец
    нажмёт в панели, ляжет в базу и не уедет никому. Ответ «в очереди ноль,
    всё тихо» на этот вопрос — зелёное по построению, худший из возможных
    отказов. Мерж без рестарта уже стоил нам эталона регресса
    ([[jarvis-stale-process-destroys-the-artifact]]), и лампа обязана его
    видеть."""


def outgoing_raw(db_path) -> dict:
    """ФАКТЫ об исходящей очереди, без единого порога и без единой трактовки:

        {"pending": int, "refused": int, "oldest_created_ts": float | None}

    Порог («застряло или нет») живёт у того, кто ОТВЕЧАЕТ владельцу, — у
    инстанса панели (`app/panel_client.py`). Здесь его нет намеренно: два числа
    на одну вещь разъезжаются молча, и меньшее гасит большее.

    Возраст тоже не считается здесь: часы у читателя свои, и `now` обязан быть
    ОДИН на весь ответ, а не два разных показания в одном теле.

    Счёт идёт по СТАТУСАМ одной группировкой, а не тремя запросами: три запроса
    к живой базе — это три разных момента времени, и сумма из них не сходится
    ровно тогда, когда очередь движется.

    🔴 `refused` — ТОЛЬКО НЕСНЯТЫЕ, и это здесь не фильтром, а СТАТУСОМ: снятый
    отказ живёт как `dismissed` и в эту группу не попадает по построению. Не
    «чинить» это добавлением `OR status='dismissed'`: в снятии весь смысл —
    владелец увидел отказ, закрыл его, и лампа погасла. Строка при этом цела и
    остаётся уликой.
    """
    # `try/finally` + явный `close()`, а НЕ `with _ro(...)`: `with` на
    # соединении sqlite закрывает ТРАНЗАКЦИЮ, а не файл — соединение живёт,
    # пока его не соберёт сборщик мусора. Эту ручку дёргает watchdog каждые 30
    # секунд весь срок жизни процесса панели, и «закроется когда-нибудь» тут
    # означает открытый хэндл файла БД неопределённое время: на Windows такой
    # хэндл делает файл занятым для всех остальных (бэкап, ротация, перенос).
    # Отказ (в том числе `OutgoingQueueMissing` ниже) обязан закрывать
    # соединение так же надёжно, как успех, — отсюда `finally`.
    conn = _ro(db_path)
    try:
        if not _table_exists(conn, "outgoing_queue"):
            raise OutgoingQueueMissing(
                "в базе %s нет таблицы outgoing_queue: раннер не перезапущен "
                "на код с очередью — отправка из панели никуда не уедет" % db_path)
        counts = {r["status"]: int(r["n"]) for r in conn.execute(
            "SELECT status, COUNT(*) AS n FROM outgoing_queue GROUP BY status")}
        oldest = conn.execute(
            "SELECT MIN(created_ts) AS t FROM outgoing_queue "
            "WHERE status='pending'").fetchone()["t"]
    finally:
        conn.close()
    return {
        "pending": counts.get("pending", 0),
        "refused": counts.get("refused", 0),
        "oldest_created_ts": None if oldest is None else float(oldest),
    }
