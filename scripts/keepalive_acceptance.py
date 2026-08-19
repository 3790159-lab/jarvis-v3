# -*- coding: utf-8 -*-
"""Приёмка Г4 keep-alive: доля холодных и цена вызова ДО и ПОСЛЕ.

Спека `2026-08-09-chatter-cache-keepalive.md` §4/§4.1.

ПОЧЕМУ ОДИНАКОВОЕ ОКНО ДНЕЙ — требование владельца 19.08 и не формальность.
Доля холодных зависит от КАДЭНСА лидов: в неделю с двумя разговорами по три
хода она одна, в неделю с одним длинным диалогом — другая, и разница между
ними больше, чем эффект арки. Сравнивая «неделю до» с «тремя днями после», мы
померили бы кадэнс, а не пинги. Поэтому окна задаются явно и равной длины, а
скрипт ОТКАЗЫВАЕТСЯ считать, если они разной.

ПОЧЕМУ НЕ СУТКИ. У volska ~140 классификаторных вызовов за всё время. На сутках
статистики не наберётся, и любой вывод будет шумом. Окно — недели; это названо
заранее, чтобы не подгонять вывод под короткий срок.

    python scripts/keepalive_acceptance.py --db .secrets/demo.db \
        --before 2026-08-13..2026-08-20 --after 2026-08-20..2026-08-27
"""
from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from chatter.core.cache_health import (          # noqa: E402
    KEEPALIVE_TAG_PREFIX, consecutive_misses, hit_rate, is_regression)

RATE_IN, RATE_OUT = 3.0, 15.0            # $/M, sonnet-5
TAGS = ("classifier", "brain")


def _parse_window(text: str) -> tuple[float, float]:
    try:
        a, b = text.split("..")
        ta = dt.datetime.strptime(a, "%Y-%m-%d").timestamp()
        tb = dt.datetime.strptime(b, "%Y-%m-%d").timestamp()
    except ValueError as exc:
        raise SystemExit(f"окно должно быть вида ГГГГ-ММ-ДД..ГГГГ-ММ-ДД: {exc}")
    if tb <= ta:
        raise SystemExit(f"окно {text}: конец не позже начала")
    return ta, tb


def _rows(db: Path, lo: float, hi: float) -> list[dict]:
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    out = [dict(r) for r in conn.execute(
        "select * from llm_usage where ts >= ? and ts < ? order by ts", (lo, hi))]
    conn.close()
    return out


def _cost(row) -> float:
    c5 = row.get("cache_creation_5m") or 0
    c1 = row.get("cache_creation_1h") or 0
    if not (c5 or c1):
        c1 = row["cache_creation_input_tokens"]
    return (row["input_tokens"] / 1e6 * RATE_IN
            + row["output_tokens"] / 1e6 * RATE_OUT
            + row["cache_read_input_tokens"] / 1e6 * RATE_IN * 0.1
            + c5 / 1e6 * RATE_IN * 1.25 + c1 / 1e6 * RATE_IN * 2.0)


def _report(rows: list[dict], tag: str) -> dict:
    same = [r for r in rows if r["tag"] == tag]
    pings = [r for r in rows if r["tag"] == KEEPALIVE_TAG_PREFIX + tag]
    cold = [r for r in same if not (r["cache_read_input_tokens"] or 0)]
    cost = sum(_cost(r) for r in same)
    return {
        "calls": len(same),
        "cold": len(cold),
        "cold_share": (len(cold) / len(same)) if same else None,
        "cost": cost,
        "per_call": (cost / len(same)) if same else None,
        "pings": len(pings),
        "ping_cost": sum(_cost(r) for r in pings),
        "streak": consecutive_misses(rows, tag),
        "regression": is_regression(rows, tag),
        "hit_rate": hit_rate(rows, tag),
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="keepalive_acceptance")
    p.add_argument("--db", required=True)
    p.add_argument("--before", required=True, help="ГГГГ-ММ-ДД..ГГГГ-ММ-ДД")
    p.add_argument("--after", help="то же; без него печатается только «до»")
    args = p.parse_args(argv)

    db = Path(args.db)
    if not db.exists():
        print(f"нет БД: {db}"); return 2
    b_lo, b_hi = _parse_window(args.before)

    if args.after:
        a_lo, a_hi = _parse_window(args.after)
        # ОТКАЗ, а не предупреждение: сравнение разных окон даёт число, которое
        # выглядит как результат арки и им не является. Такую строку в отчёте
        # не отличить от настоящей — значит её не должно быть вовсе.
        if abs((b_hi - b_lo) - (a_hi - a_lo)) > 1.0:
            print(f"ОТКАЗ: окна разной длины — «до» {(b_hi-b_lo)/86400:.2f} сут, "
                  f"«после» {(a_hi-a_lo)/86400:.2f} сут. Доля холодных зависит "
                  f"от кадэнса лидов сильнее, чем от пингов: разные окна "
                  f"померят кадэнс, а не арку (§4.1).")
            return 2

    windows = [("ДО", b_lo, b_hi)]
    if args.after:
        windows.append(("ПОСЛЕ", a_lo, a_hi))

    reports = {}
    for name, lo, hi in windows:
        rows = _rows(db, lo, hi)
        days = (hi - lo) / 86400
        print(f"═══ {name}: {dt.datetime.fromtimestamp(lo):%Y-%m-%d} … "
              f"{dt.datetime.fromtimestamp(hi):%Y-%m-%d} ({days:.1f} сут), "
              f"строк {len(rows)}")
        for tag in TAGS:
            r = _report(rows, tag)
            reports[(name, tag)] = r
            if not r["calls"]:
                print(f"   {tag:11} вызовов НЕТ — окно пустое, сравнивать нечего")
                continue
            print(f"   {tag:11} вызовов {r['calls']:4}, ХОЛОДНЫХ {r['cold']:3} "
                  f"({r['cold_share']:.1%}), ${r['per_call']:.5f}/вызов, "
                  f"всего ${r['cost']:.4f}")
            if r["pings"]:
                print(f"   {'':11} пингов {r['pings']:4} на ${r['ping_cost']:.4f} "
                      f"({r['ping_cost']/max(r['cost'],1e-9):.1%} от статьи)")
            if r["regression"]:
                print(f"   {'':11} 🔴 РЕГРЕССИЯ: {r['streak']} промахов подряд "
                      f"при живом кэше")
        print()

    if not args.after:
        return 0

    print("═══ ВЕРДИКТ (гейты §4)")
    ok = True
    for tag in TAGS:
        b, a = reports[("ДО", tag)], reports[("ПОСЛЕ", tag)]
        if not (b["calls"] and a["calls"]):
            print(f"   {tag}: НЕ СОСТОЯЛОСЬ — в одном из окон нет вызовов")
            ok = False
            continue
        saved = (b["per_call"] - a["per_call"]) * a["calls"]
        net = saved - a["ping_cost"]
        print(f"   {tag}: холодных {b['cold_share']:.1%} → {a['cold_share']:.1%} "
              f"(гейт ≤5%){'  ✅' if a['cold_share'] <= 0.05 else '  ❌'}")
        print(f"   {'':{len(tag)}}  $/вызов {b['per_call']:.5f} → {a['per_call']:.5f}")
        print(f"   {'':{len(tag)}}  сэкономлено ${saved:.4f}, пинги "
              f"${a['ping_cost']:.4f}, ЧИСТО ${net:+.4f}"
              f"{'  ✅' if net > 0 else '  ❌ откатывать'}")
        ok = ok and a["cold_share"] <= 0.05 and net > 0
    print()
    print("ГЕЙТ Г4 " + ("ЗЕЛЁНЫЙ" if ok else "КРАСНЫЙ"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
