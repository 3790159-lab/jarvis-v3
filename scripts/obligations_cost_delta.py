"""Замер дельты стоимости слота обязательств (спека §8, гейт ≤ +10%).

Читает llm_usage из БД, делит по --split-ts (epoch включения флага) на ДО/ПОСЛЕ,
печатает средний $/ход (brain+classifier[+retry]) и дельту %. «Ход» = один
brain-вызов (классификатор и ретрай приписываются тому же ходу по порядку ts).

Два правила окна — оба выведены живым дрилом Д-10 2026-07-24, где наивный замер
печатал +15.3% при реальных +2.1%:

* **Окно = текущий день** (`--since` по умолчанию = сегодня 00:00). Ходы прошлых
  дней шли на ДРУГОМ промпте/персоне и baseline'ом быть не могут. Полная история
  доступна явным `--since 0` — но это уже не гейт арки, а тренд.
* **Cold-start ходы исключаются** из обеих сторон. Ход на ХОЛОДНОМ кэше платит
  cache_creation вместо cache_read (brain $0.040 вместо $0.011): один такой ход
  в ON-окне из восьми задирал среднее на +13%. Признак — только
  `cache_creation_input_tokens > 0` НА BRAIN-вызове; у классификатора
  cache_read=0 всегда (он кэш не читает вообще), это не признак холода. Сколько
  ходов отброшено — печатается всегда, молчаливого усечения нет. Вернуть старое
  поведение: `--include-cold`.

  ⚠️ Холодный кэш — это ИСТЁКШИЙ TTL (разрыв между ходами > 1ч), а НЕ рестарт
  раннера: кэш живёт на стороне API и переживает перезапуск процесса. Проверено
  фактом 2026-07-25 — раннер рестартовал в 01:16, а brain в 01:17 прочитал кэш
  (cr=8801) через 38 минут после предыдущего хода. Не отбрасывай ходы «потому
  что рядом был рестарт»: маркер ровно один — cache_creation на brain.

Тарифы Anthropic ($/M токенов, стандарт с 2026-09-01): input 3, output 15,
cache_read 0.30, cache_write 3.75 (5m) / 6.0 (1h). Ставка записи берётся
ПОСТРОЧНО из разбивки (фаза 0 арки «кэш классификатора»); строки без разбивки
считаются по 5m и помечаются в отчёте как оценка. Факт 2026-07-25: живой раннер
пишет по ставке 1h — llm.py ставит `ttl:"1h"`, и API это применяет.

    python scripts/obligations_cost_delta.py --db .secrets/demo.db --split-ts <ts_flip>
"""
from __future__ import annotations

import argparse
import datetime
import sqlite3
from dataclasses import dataclass, field

RATE_IN, RATE_OUT, RATE_CR = 3.0, 15.0, 0.30           # $/M
RATE_CW_5M, RATE_CW_1H = 3.75, 6.0                     # запись кэша: x1.25 / x2

_TAGS = ("brain", "classifier", "classifier_retry")
GATE_PCT = 10.0


def default_since() -> float:
    """Начало ТЕКУЩЕГО дня (local): baseline меряем на том же промпте, что и ON."""
    return datetime.datetime.combine(
        datetime.date.today(), datetime.time.min).timestamp()


def _keys(r) -> set:
    return set(r.keys()) if hasattr(r, "keys") else set()


def _write_cost(r) -> tuple[float, bool]:
    """($ записи кэша, была ли разбивка по TTL).

    Ставки записи РАЗНЫЕ: 5m = $3.75/M, 1h = $6/M. Пока считали всё по 5m,
    занижали стоимость классификатора на треть (он пишет ~7.8К ток каждый ход
    при `ttl:1h` в llm.py). Строки старше фазы 0 разбивки не имеют — для них
    остаётся прежняя ставка, но отчёт об этом ГОВОРИТ: смешивать факт с
    оценкой молча нельзя, замер ДО/ПОСЛЕ на этом и поедет."""
    cols = _keys(r)
    m5 = r["cache_creation_5m"] if "cache_creation_5m" in cols else None
    h1 = r["cache_creation_1h"] if "cache_creation_1h" in cols else None
    if (m5 or 0) + (h1 or 0) > 0:
        return ((m5 or 0) * RATE_CW_5M + (h1 or 0) * RATE_CW_1H) / 1_000_000, False
    return r["cache_creation_input_tokens"] * RATE_CW_5M / 1_000_000, \
        r["cache_creation_input_tokens"] > 0


def _cost(r) -> tuple[float, bool]:
    """($ вызова, посчитан ли он по ОЦЕНОЧНОЙ ставке записи)."""
    write, legacy = _write_cost(r)
    return (r["input_tokens"] * RATE_IN
            + r["output_tokens"] * RATE_OUT
            + r["cache_read_input_tokens"] * RATE_CR) / 1_000_000 + write, legacy


@dataclass
class _Turn:
    ts: float
    cold: bool
    cost: float = 0.0


@dataclass
class Report:
    since: float
    until: float
    split_ts: float
    include_cold: bool
    off_turns: int = 0
    on_turns: int = 0
    off_cold: int = 0
    on_cold: int = 0
    off_total: float = 0.0
    on_total: float = 0.0
    legacy_write_rows: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def off_avg(self) -> float:
        return self.off_total / self.off_turns if self.off_turns else 0.0

    @property
    def on_avg(self) -> float:
        return self.on_total / self.on_turns if self.on_turns else 0.0

    @property
    def delta(self) -> float | None:
        """Прирост $/ход в %, или None если baseline пуст (NaN врал зелёным)."""
        if not self.off_turns or not self.on_turns or self.off_avg == 0:
            return None
        return (self.on_avg - self.off_avg) / self.off_avg * 100

    @property
    def passed(self) -> bool | None:
        d = self.delta
        return None if d is None else d <= GATE_PCT

    def render(self) -> str:
        def _hm(ts: float) -> str:
            return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")

        lines = [
            f"окно: {_hm(self.since)} … {'∞' if self.until >= 1e17 else _hm(self.until)}"
            f"   split: {_hm(self.split_ts)}",
            f"OFF (baseline, до split): {self.off_turns:3d} ходов  ${self.off_avg:.4f}/ход  "
            f"(итого ${self.off_total:.3f})",
            f"ON  (слот включён):       {self.on_turns:3d} ходов  ${self.on_avg:.4f}/ход  "
            f"(итого ${self.on_total:.3f})",
        ]
        if self.include_cold:
            lines.append(f"cold-start ходы ВКЛЮЧЕНЫ (--include-cold): "
                         f"OFF {self.off_cold}, ON {self.on_cold} — замер завышен на цену "
                         f"прогрева кэша, гейтом не считать")
        else:
            lines.append(f"исключено cold-start ходов (первый после рестарта раннера): "
                         f"OFF {self.off_cold}, ON {self.on_cold}")
        d = self.delta
        if d is None:
            lines.append(f"ДЕЛЬТА: н/д — нет baseline в окне "
                         f"(OFF={self.off_turns}, ON={self.on_turns}). Сузь --split-ts "
                         f"или расширь --since.")
        else:
            verdict = "OK" if self.passed else "ПРЕВЫШЕН — режь RECENT_DAYS 7→3"
            lines.append(f"ДЕЛЬТА: {d:+.1f}%   гейт ≤ +{GATE_PCT:.0f}%   {verdict}")
        if self.legacy_write_rows:
            lines.append(
                f"⚠ {self.legacy_write_rows} вызов(ов) без разбивки cache_creation по "
                f"TTL — их запись посчитана по ставке 5m (${RATE_CW_5M}/M): это оценка, "
                f"не факт. "
                f"При ttl:1h реальная ставка ${RATE_CW_1H}/M, то есть эта часть занижена "
                f"до {(RATE_CW_1H / RATE_CW_5M - 1) * 100:.0f}%.")
        lines.extend(self.warnings)
        return "\n".join(lines)


def report(db: str, *, split_ts: float, since: float | None = None,
           until: float = 1e18, include_cold: bool = False) -> Report:
    if since is None:
        since = default_since()
    rep = Report(since=since, until=until, split_ts=split_ts,
                 include_cold=include_cold)

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM llm_usage WHERE ts >= ? AND ts < ? "
            "AND tag IN ('brain','classifier','classifier_retry') ORDER BY ts",
            (since, until)).fetchall()
    finally:
        conn.close()

    # Ход открывается brain-вызовом; classifier/retry до следующего brain —
    # его хвост. Хвост без своего brain (обрезан границей окна) отбрасываем:
    # приписать его чужому ходу — исказить $/ход.
    turns: list[_Turn] = []
    cur: _Turn | None = None
    orphan_tail = 0
    for r in rows:
        cost, legacy = _cost(r)
        if legacy:
            rep.legacy_write_rows += 1
        if r["tag"] == "brain":
            cur = _Turn(ts=r["ts"], cold=r["cache_creation_input_tokens"] > 0,
                        cost=cost)
            turns.append(cur)
        elif cur is not None:
            cur.cost += cost
        else:
            orphan_tail += 1
    if orphan_tail:
        rep.warnings.append(
            f"⚠ {orphan_tail} вызов(ов) классификатора без brain в окне — "
            f"отброшены (хвост хода, начавшегося до --since)")

    for t in turns:
        on = t.ts >= split_ts
        if t.cold:
            if on:
                rep.on_cold += 1
            else:
                rep.off_cold += 1
            if not include_cold:
                continue
        if on:
            rep.on_turns += 1
            rep.on_total += t.cost
        else:
            rep.off_turns += 1
            rep.off_total += t.cost
    return rep


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=".secrets/demo.db")
    ap.add_argument("--split-ts", type=float, required=True,
                    help="epoch включения CHATTER_OBLIGATIONS_SLOT")
    ap.add_argument("--since", type=float, default=None,
                    help="epoch начала окна (по умолчанию — сегодня 00:00; "
                         "0 = вся история, но это тренд, а не гейт)")
    ap.add_argument("--until", type=float, default=1e18)
    ap.add_argument("--include-cold", action="store_true",
                    help="не исключать первый ход после рестарта раннера "
                         "(прогрев кэша задирает $/ход в ~3.6 раза)")
    a = ap.parse_args()

    print(report(a.db, split_ts=a.split_ts, since=a.since, until=a.until,
                 include_cold=a.include_cold).render())


if __name__ == "__main__":
    main()
