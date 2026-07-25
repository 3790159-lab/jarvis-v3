"""Дрил-харнесс v1: суфлёр живого дрила (спека 2026-07-25).

Что делает: на каждом шаге сценария печатает (и присылает карточкой) реплику
для отправки Ольге, ждёт, что ход случился, снимает факты из БД и лога, считает
вердикт по записанным ожиданиям и складывает отчёт. Отправку реплики
по-прежнему делает человек — автоматика лида это v2 (отдельный аккаунт).

🔴 ГЛАВНЫЙ ИНВАРИАНТ: харнесс НЕ открывает long-poll Telegram. Раннер УЖЕ
поллит контрол-бота, а второй потребитель на том же токене получает
409 Conflict — упал бы ЖИВОЙ пульт владельца, а не только дрил. Поэтому тап
кнопки принимает поллер раннера (пишет `runtime_flags`), а харнесс читает БД.
Сторож этого инварианта — `tests/test_drill_runner_script.py`.

Дрил идёт на ЖИВОМ раннере: ничего не рестартуем, в клиентскую БД не пишем
(всё чтение — `mode=ro`).

    python scripts/drill_runner.py docs/chatter/drills/d10.yaml --db .secrets/demo.db
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sqlite3
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from chatter.core.drill import (  # noqa: E402
    CheckResult, Facts, check_step, parse_scenario,
)

logger = logging.getLogger("jarvis.drill_runner")

# Средняя цена хода на 2026-07-25 ПОСЛЕ починки кэша классификатора (замер:
# brain $0.0120 + классификатор $0.0218 амортизированно). Смета — оценка, факт
# считается после прогона по llm_usage.
COST_PER_TURN = 0.0352
CONFIRM_THRESHOLD_USD = 0.50
STEP_TIMEOUT_SEC = 15 * 60
POLL_SEC = 2.0

RATE_IN, RATE_OUT, RATE_CR, RATE_CW5, RATE_CW1H = 3.0, 15.0, 0.30, 3.75, 6.0


def _ro(db: str):
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


def _has_table(conn, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


# ── факты хода (только чтение) ───────────────────────────────────────────────


def collect_facts(db: str, *, contact: str, since_ts: float,
                  log_lines: list[str], before: dict) -> Facts:
    conn = _ro(db)
    conn.row_factory = sqlite3.Row
    try:
        clf = conn.execute(
            "SELECT cache_read_input_tokens cr FROM llm_usage "
            "WHERE ts >= ? AND tag='classifier' ORDER BY ts DESC LIMIT 1",
            (since_ts,)).fetchone()
        # «н/д» ≠ «промах»: ход мог вообще не дойти до классификатора, и
        # объявлять это промахом кэша значило бы врать в отчёте.
        cache = "n/a" if clf is None else ("hit" if (clf["cr"] or 0) > 0 else "miss")

        obligations = {r["okey"]: r["status"] for r in conn.execute(
            "SELECT okey, status FROM contact_obligations WHERE contact_id=?",
            (contact,))}

        prof = conn.execute(
            "SELECT text FROM contact_profile WHERE contact_id=? ORDER BY rowid DESC LIMIT 1",
            (contact,)).fetchone()

        errors = conn.execute(
            "SELECT COUNT(*) n FROM control_events WHERE ts >= ? AND "
            "kind IN ('classifier_error','classifier_recovered')", (since_ts,)).fetchone()["n"]

        cards = conn.execute(
            "SELECT COUNT(*) n FROM console_cards WHERE ts >= ?", (since_ts,)).fetchone()["n"]
    finally:
        conn.close()

    return Facts(
        cache=cache, obligations=obligations, obligations_before=dict(before),
        profile=(prof["text"] if prof else ""), classifier_errors=int(errors),
        cards_delivered=int(cards),
        replies=sum(1 for ln in log_lines if ": OUT " in ln),
        process_ends=sum(1 for ln in log_lines if "process END" in ln))


def obligations_snapshot(db: str, contact: str) -> dict:
    conn = _ro(db)
    conn.row_factory = sqlite3.Row
    try:
        if not _has_table(conn, "contact_obligations"):
            return {}
        return {r["okey"]: r["status"] for r in conn.execute(
            "SELECT okey, status FROM contact_obligations WHERE contact_id=?", (contact,))}
    finally:
        conn.close()


# ── сигнал «ход случился»: сообщение лида ИЛИ тап кнопки ─────────────────────


def step_signal_seen(db: str, *, contact: str, since_ts: float, flag_key: str) -> bool:
    """Настоящий сигнал — входящее лида; тап кнопки — запасной путь, если
    сообщение не долетело. Ни один не блокирует другой: забыл тапнуть — прогон
    всё равно едет."""
    conn = _ro(db)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT 1 FROM messages WHERE contact_id=? AND role='user' AND ts > ? LIMIT 1",
            (contact, since_ts)).fetchone()
        if row:
            return True
        if _has_table(conn, "runtime_flags"):
            f = conn.execute("SELECT value FROM runtime_flags WHERE key=?",
                             (flag_key,)).fetchone()
            if f and str(f["value"]).strip() not in ("", "0"):
                return True
        return False
    finally:
        conn.close()


# ── деньги ───────────────────────────────────────────────────────────────────


def estimate_cost(*, steps: int, per_turn: float = COST_PER_TURN) -> float:
    return steps * per_turn


def format_estimate(steps: int, est: float) -> str:
    return (f"смета прогона: {steps} шаг(ов) × ${COST_PER_TURN:.4f} ≈ ${est:.2f} "
            f"(факт посчитаю после прогона по llm_usage)")


def measure_spend(db: str, *, since_ts: float) -> float:
    """Факт по llm_usage со ставкой записи ПОСТРОЧНО (5m $3.75/M против
    1h $6/M) — та же методика, что в obligations_cost_delta.py."""
    conn = _ro(db)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM llm_usage WHERE ts >= ?", (since_ts,)).fetchall()
    finally:
        conn.close()
    total = 0.0
    for r in rows:
        keys = set(r.keys())
        m5 = (r["cache_creation_5m"] if "cache_creation_5m" in keys else 0) or 0
        h1 = (r["cache_creation_1h"] if "cache_creation_1h" in keys else 0) or 0
        write = (m5 * RATE_CW5 + h1 * RATE_CW1H) if (m5 or h1) else \
            (r["cache_creation_input_tokens"] or 0) * RATE_CW5
        total += (r["input_tokens"] * RATE_IN + r["output_tokens"] * RATE_OUT
                  + r["cache_read_input_tokens"] * RATE_CR + write) / 1_000_000
    return total


# ── отчёт ────────────────────────────────────────────────────────────────────


def format_report(name: str, steps: list[dict], *, spent: float) -> str:
    lines = [f"# Дрил: {name}", ""]
    for i, st in enumerate(steps, 1):
        checks: list[CheckResult] = st["checks"]
        mark = "✅" if all(c.ok for c in checks) else "🔴"
        if not checks:
            mark = "➖"
        lines.append(f"## {mark} Шаг {i}: «{st['say']}»")
        for c in checks:
            lines.append(f"- {'✅' if c.ok else '🔴'} `{c.key}` — {c.detail}")
        if st.get("note"):
            lines.append(f"- ⚠️ {st['note']}")
        lines.append("")
    lines.append(f"**Потрачено за прогон:** ${spent:.4f}")
    return "\n".join(lines)


# ── живой шов ────────────────────────────────────────────────────────────────


def backup_log(log_path: Path) -> Path | None:
    """Раннер УСЕКАЕТ свой лог при каждом старте — без .bak форензика прогона
    исчезнет при первом же респавне гардианом."""
    if not log_path.is_file():
        return None
    dest = log_path.with_name(f"{log_path.name}.drill-{int(time.time())}.bak")
    shutil.copy2(log_path, dest)
    return dest


def _read_log_since(log_path: Path, offset: int) -> tuple[list[str], int]:
    if not log_path.is_file():
        return [], offset
    data = log_path.read_text(encoding="utf-8", errors="replace")
    return data[offset:].splitlines(), len(data)


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("--db", default=str(_ROOT / ".secrets" / "demo.db"))
    ap.add_argument("--log", default=str(_ROOT / "logs" / "chatter_volska.log"))
    ap.add_argument("--out", default=str(_ROOT / "state" / "drills"))
    ap.add_argument("--yes", action="store_true",
                    help="ЗАПУСТИТЬ прогон (без флага печатается только план)")
    ap.add_argument("--dry-run", action="store_true",
                    help="напечатать план и смету, ничего не делать")
    ap.add_argument("--step-timeout", type=float, default=STEP_TIMEOUT_SEC)
    a = ap.parse_args(argv)

    sc = parse_scenario(Path(a.scenario).read_text(encoding="utf-8"))
    est = estimate_cost(steps=len(sc.steps))
    print(format_estimate(len(sc.steps), est))
    for i, s in enumerate(sc.steps, 1):
        print(f"  {i}. «{s.say}» → {', '.join(sorted(s.expect)) or '(без проверок)'}")

    # Запуск ТОЛЬКО по явному --yes. Дрил стоит живых денег и требует действий
    # человека у телефона: прогон «просто посмотреть, что за скрипт» не должен
    # молча уходить в ожидание сигналов по 15 минут на шаг (поймано на себе при
    # первой же обкатке 2026-07-25).
    if a.dry_run or not a.yes:
        print("\nэто ПЛАН. Запуск: добавь --yes (и будь у телефона — "
              "каждый шаг ждёт твоей реплики Ольге)")
        return 0

    log_path = Path(a.log)
    bak = backup_log(log_path)
    if bak:
        print(f"лог сохранён: {bak.name}")

    run_start = time.time()
    before = obligations_snapshot(a.db, sc.contact)
    print(f"снимок слота ДО прогона: {before or '(пусто)'}")
    results: list[dict] = []
    _, offset = _read_log_since(log_path, 0)

    for i, step in enumerate(sc.steps, 1):
        print(f"\n=== ШАГ {i}/{len(sc.steps)} — отправь Ольге: ===\n{step.say}\n")
        step_start = time.time()
        flag_key = f"drill:{i}"
        deadline = step_start + a.step_timeout
        note = None
        while time.time() < deadline:
            if step_signal_seen(a.db, contact=sc.contact, since_ts=step_start,
                                flag_key=flag_key):
                break
            time.sleep(POLL_SEC)
        else:
            note = f"шаг пропущен: сигнала не было {a.step_timeout / 60:.0f} мин"
            print(f"⚠️ {note}")
            results.append({"say": step.say, "checks": [], "note": note})
            continue

        # Ход мог ещё договаривать баббл — дадим ему закрыться по process END.
        for _ in range(60):
            lines, _ = _read_log_since(log_path, offset)
            if any("process END" in ln for ln in lines):
                break
            time.sleep(POLL_SEC)
        lines, offset = _read_log_since(log_path, offset)
        facts = collect_facts(a.db, contact=sc.contact, since_ts=step_start,
                             log_lines=lines, before=before)
        checks = check_step(step.expect, facts)
        for c in checks:
            print(f"  {'✅' if c.ok else '🔴'} {c.key}: {c.detail}")
        results.append({"say": step.say, "checks": checks, "note": note})
        before = facts.obligations

    spent = measure_spend(a.db, since_ts=run_start)
    report = format_report(sc.name, results, spent=spent)
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{int(run_start)}.md"
    out_file.write_text(report, encoding="utf-8")
    print(f"\n{report}\n\nотчёт: {out_file}")
    failed = sum(1 for st in results for c in st["checks"] if not c.ok)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
