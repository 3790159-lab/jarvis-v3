"""Замер дельты стоимости слота обязательств (спека §8, гейт ≤ +10%).

Читает llm_usage из БД, делит по --split-ts (epoch включения флага) на ДО/ПОСЛЕ,
печатает средний $/ход (brain+classifier[+retry]) и дельту %. «Ход» = один
brain-вызов (классификатор и ретрай приписываются тому же окну по ts).

Тарифы Anthropic ($/M токенов, стандарт до промо): input 3, output 15,
cache_read 0.30, cache_write(creation) 3.75. Абсолютные $ — оценка; дельта %
устойчива к тарифу (обе стороны считаются одинаково).

    python scripts/obligations_cost_delta.py --db .secrets/demo.db --split-ts <ts_flip>
"""
from __future__ import annotations

import argparse
import sqlite3

RATE_IN, RATE_OUT, RATE_CR, RATE_CW = 3.0, 15.0, 0.30, 3.75  # $/M

_TAGS = ("brain", "classifier", "classifier_retry")


def _cost(r) -> float:
    return (r["input_tokens"] * RATE_IN
            + r["output_tokens"] * RATE_OUT
            + r["cache_read_input_tokens"] * RATE_CR
            + r["cache_creation_input_tokens"] * RATE_CW) / 1_000_000


def _window(conn, lo: float, hi: float):
    rows = conn.execute(
        "SELECT * FROM llm_usage WHERE ts >= ? AND ts < ? "
        "AND tag IN ('brain','classifier','classifier_retry')", (lo, hi)).fetchall()
    total = sum(_cost(r) for r in rows)
    turns = sum(1 for r in rows if r["tag"] == "brain")
    return total, turns


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=".secrets/demo.db")
    ap.add_argument("--split-ts", type=float, required=True,
                    help="epoch включения CHATTER_OBLIGATIONS_SLOT")
    ap.add_argument("--since", type=float, default=0.0)
    ap.add_argument("--until", type=float, default=1e18)
    a = ap.parse_args()

    conn = sqlite3.connect(a.db)
    conn.row_factory = sqlite3.Row
    off_total, off_turns = _window(conn, a.since, a.split_ts)
    on_total, on_turns = _window(conn, a.split_ts, a.until)
    off_avg = off_total / off_turns if off_turns else 0.0
    on_avg = on_total / on_turns if on_turns else 0.0
    delta = (on_avg - off_avg) / off_avg * 100 if off_avg else float("nan")

    print(f"OFF (baseline, до split): {off_turns:3d} ходов  ${off_avg:.4f}/ход  "
          f"(итого ${off_total:.3f})")
    print(f"ON  (слот включён):       {on_turns:3d} ходов  ${on_avg:.4f}/ход  "
          f"(итого ${on_total:.3f})")
    print(f"ДЕЛЬТА: {delta:+.1f}%   гейт ≤ +10%   "
          f"{'OK' if delta <= 10 else 'ПРЕВЫШЕН — режь RECENT_DAYS 7→3'}")


if __name__ == "__main__":
    main()
