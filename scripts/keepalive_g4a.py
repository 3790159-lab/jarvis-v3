# -*- coding: utf-8 -*-
"""Г4-а: приёмка МЕХАНИКИ keep-alive на синтетическом кадэнсе.

Спека `2026-08-09-chatter-cache-keepalive.md` §4.1. Владелец 20.08 развёл
приёмку надвое: Г4-а — механика на синтетике, и её достаточно для МЕРЖА;
Г4-б — экономика на живом трафике, ждёт первого настоящего клиента.

ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ И ЧТО НЕТ. Проверяется РАСПИСАНИЕ: уходят ли пинги тогда,
когда должны, попадают ли в окно клиента, переживает ли кэш-запись паузу между
разговорами, падает ли доля холодных ходов. НЕ проверяется физика кэша
Anthropic — она закрыта живыми замерами Г1/Г2 от 2026-08-10 («TTL продлевается
от ПОСЛЕДНЕГО ИСПОЛЬЗОВАНИЯ, а не от момента записи»). Отсюда и модель кэша
здесь: запись жива, если последнее КАСАНИЕ было меньше TTL назад. Это не моя
догадка, а перенесённый сюда результат замера — иначе харнесс соглашался бы сам
с собой по построению.

ЦИКЛ БЕРЁТСЯ НАСТОЯЩИЙ. `run_keepalive_cycle` — тот же, что пойдёт в прод, и
Store настоящий (временная база). Подменён только собеседник: пинг не уходит в
сеть. Если бы я написал своё расписание рядом, приёмка проверяла бы копию, а не
предмет.

ДВА ПЛЕЧА ОБЯЗАТЕЛЬНЫ. Считается доля холодных БЕЗ пингов и С пингами на ОДНОМ
И ТОМ ЖЕ кадэнсе. Одно плечо ничего не значит: доля холодных зависит от кадэнса
сильнее, чем от арки, и «14% с пингами» без «сколько было бы без них» — число
ни о чём.

Прогон: .venv\\Scripts\\python.exe scripts/keepalive_g4a.py
"""
from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import os
import sys
import tempfile
from pathlib import Path

# Стенд обязан ЗЕРКАЛИТЬ прод, а не удобную себе среду. Гардиан ставит этот
# флаг (`scripts/chatter_guardian_detached.ps1:105`), и без него классификатор
# собирает системный промпт ВМЕСТЕ с профилем лида — то есть кэшируемого
# префикса у него нет вовсе, и цикл честно отказывается его греть. Первый
# прогон Г4-а без флага грел ОДИН префикс из двух и выглядел вдвое скромнее.
os.environ.setdefault("CHATTER_CLASSIFIER_CACHE", "1")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from chatter.config.loader import load_config          # noqa: E402
from chatter.core.cache_health import CACHE_TTL_SEC    # noqa: E402
from chatter.core import keepalive as ka               # noqa: E402
from chatter.storage.db import Store                   # noqa: E402

# ── Синтетический кадэнс ─────────────────────────────────────────────────────
# Три разговора в сутки в разное время, девять ходов в каждом, ход = ДВА вызова
# (brain отвечает, классификатор судит тот же ход — проверено по боевому
# леджеру volska: 145 и 140, один к одному).
#
# Часы выбраны ВНУТРИ окна `09:00-22:00` намеренно: Г4-а меряет механику
# прогрева, а не отсечение по окну — его стерегут сторожа. Один разговор в
# 21:30 оставлен у самой границы: если окно считается неверно, он выпадет.
DAYS = 14
DIALOG_HOURS = (10.5, 15.0, 21.5)
MOVES_PER_DIALOG = 9
MOVE_GAP_S = 90.0
TICK_S = 300.0                      # тот же тик, что в telethon_run
BASELINE_COLD_SHARE = 22.4          # замер Г0, §4 спеки

DAY = 86400.0

# ⚠️ Полночь ВЫЧИСЛЯЕТСЯ, а не зашивается числом. Первая редакция взяла
# «красивую» константу 1786752000, которая на этой машине оказалась 03:00, и
# третий разговор уезжал на 00:30 — за окно клиента. Плечо с пингами при этом
# выглядело бы хуже, чем оно есть, и Г4-а провалился бы по вине стенда.
# Константа времени без часового пояса — это всегда догадка.
T0 = dt.datetime(2026, 8, 17, 0, 0, 0).timestamp()   # понедельник, местная


class CacheModel:
    """Модель кэш-записи по замеру Г2: жива, пока касание было < TTL назад."""

    def __init__(self) -> None:
        self.last_touch: dict[str, float] = {}

    def touch(self, key: str, now: float) -> bool:
        """Касание. Возвращает True, если запись БЫЛА жива (то есть попали)."""
        prev = self.last_touch.get(key)
        warm = prev is not None and (now - prev) < CACHE_TTL_SEC
        self.last_touch[key] = now
        return warm


class PingLLM:
    """Собеседник только для пинга: сети нет, физика — из CacheModel."""

    def __init__(self, cache: CacheModel, model: str) -> None:
        self.cache = cache
        self.model = model
        self.sent: list[tuple[float, str, int]] = []
        self.prev_touch: dict[tuple[str, float], float | None] = {}
        self.now = 0.0

    def send_raw(self, payload: dict, *, tag: str) -> dict:
        key = tag.replace(ka.KEEPALIVE_TAG_PREFIX, "")
        self.prev_touch[(tag, self.now)] = self.cache.last_touch.get(key)
        warm = self.cache.touch(key, self.now)
        read = 8000 if warm else 0
        self.sent.append((self.now, tag, read))
        return {"tag": tag, "model": self.model,
                "input_tokens": 12, "output_tokens": 0,
                "cache_read_input_tokens": read,
                "cache_creation_input_tokens": 0 if warm else 8000,
                "cache_creation_5m": 0,
                "cache_creation_1h": 0 if warm else 8000}


def lead_calls() -> list[tuple[float, str]]:
    """Моменты боевых вызовов: (ts, боевой тег). Один список на оба плеча —
    иначе плечи мерили бы разный кадэнс, а не разный режим."""
    out: list[tuple[float, str]] = []
    for day in range(DAYS):
        for hour in DIALOG_HOURS:
            start = T0 + day * DAY + hour * 3600.0
            for move in range(MOVES_PER_DIALOG):
                t = start + move * MOVE_GAP_S
                out.append((t, "brain"))
                out.append((t + 1.0, "classifier"))
    return sorted(out)


def arm(cfg, *, keepalive_on: bool) -> dict:
    """Одно плечо. Возвращает счётчики."""
    cache = CacheModel()
    tmp = Path(tempfile.mkdtemp()) / "g4a.db"
    store = Store(tmp)
    llm = PingLLM(cache, cfg.settings.model)

    prev_touch = llm.prev_touch
    calls = lead_calls()
    # Контакты заводятся ПО-НАСТОЯЩЕМУ: порог объёма читает `messages`, и без
    # них цикл честно отказывался пинговать (первый прогон дал ноль пингов).
    # Заводим их так, чтобы клиент проходил ДЕФОЛТНЫЙ порог 25 диал/мес, а не
    # приспущенный — иначе Г4-а обходил бы ровно ту проверку, которая в бою
    # решает, включать ли режим.
    for day in range(DAYS):
        for i, hour in enumerate(DIALOG_HOURS):
            ts = T0 + day * DAY + hour * 3600.0
            store.add_message(f"lead{day}-{i}", "user", "привет", ts)

    cold = 0
    total = 0
    next_tick = T0

    for ts, tag in calls:
        if keepalive_on:
            # Догоняем тики до момента вызова лида — так они и чередуются в бою.
            while next_tick <= ts:
                llm.now = next_tick
                asyncio.run(ka.run_keepalive_cycle(cfg, store, llm,
                                                   now=next_tick))
                next_tick += TICK_S
        warm = cache.touch(tag, ts)
        total += 1
        if not warm:
            cold += 1
        # Боевой вызов обязан попасть в леджер: по нему цикл и решает,
        # нужен ли пинг. Без этого пинги шли бы поверх живого трафика.
        store.add_llm_usage(ts=ts, tag=tag, model=cfg.settings.model,
                            input_tokens=8000, output_tokens=120,
                            cache_read_input_tokens=8000 if warm else 0,
                            cache_creation_input_tokens=0 if warm else 8000,
                            cache_creation_5m=0,
                            cache_creation_1h=0 if warm else 8000)

    tags = {t for _, t, _ in llm.sent}
    # Промахи РАЗВОДЯТСЯ ПО ПОРОДАМ — тем же правилом, что в §5 спеки.
    # Промах после разрыва >= TTL значит, что запись умерла ЗАКОННО: окно
    # клиента 13ч, ночью пингов нет, и первый утренний пинг неизбежно
    # пересоздаёт запись. Считать это дефектом — значит требовать от арки
    # круглосуточной работы, которую владелец сознательно не покупал.
    # Промах при разрыве < TTL — совсем другое дело: запись ОБЯЗАНА была быть
    # жива, и это ровно та улика, ради которой написан алерт.
    legit, broken = 0, 0
    for t, tg, r in llm.sent:
        if r:
            continue
        prev = prev_touch.get((tg, t))
        if prev is None or (t - prev) >= CACHE_TTL_SEC:
            legit += 1
        else:
            broken += 1
    return {"cold": cold, "total": total,
            "share": 100.0 * cold / total if total else 0.0,
            "pings": len(llm.sent), "ping_tags": sorted(tags),
            "miss_legit": legit, "miss_broken": broken,
            "night_writes": legit}


def main() -> int:
    cfg = load_config(ROOT / "chatter" / "clients", "demo")
    # Тумблер включаем В ПАМЯТИ, файл клиента не трогаем: включённая в файле
    # фича поднялась бы на ближайшем рестарте БЕЗ команды владельца — ровно
    # та причина, по которой у payments и funnel_gate тумблер в файле = ФАКТ.
    ka_cfg = dataclasses.replace(cfg.settings.keepalive, enabled=True)
    on = dataclasses.replace(cfg, settings=dataclasses.replace(
        cfg.settings, keepalive=ka_cfg))

    off_res = arm(cfg, keepalive_on=False)
    on_res = arm(on, keepalive_on=True)

    print("Г4-а — МЕХАНИКА KEEP-ALIVE НА СИНТЕТИЧЕСКОМ КАДЭНСЕ")
    print(f"  кадэнс: {DAYS} сут x {len(DIALOG_HOURS)} диалога x "
          f"{MOVES_PER_DIALOG} ходов, ход = 2 вызова")
    print(f"  окно клиента: {cfg.settings.keepalive.window}, "
          f"тик {TICK_S:.0f}с, период пинга {ka.PING_PERIOD_SEC}с")
    print(f"  порог объёма: {cfg.settings.keepalive.min_dialogs_per_month} "
          f"диал/мес (дефолт, НЕ приспущен), заведено контактов "
          f"{DAYS * len(DIALOG_HOURS)}")
    print(f"  первый разговор суток в "
          f"{dt.datetime.fromtimestamp(T0 + DIALOG_HOURS[0] * 3600):%H:%M}, "
          f"последний в "
          f"{dt.datetime.fromtimestamp(T0 + DIALOG_HOURS[-1] * 3600):%H:%M}")
    print()
    print(f"  БЕЗ пингов : холодных {off_res['cold']}/{off_res['total']} = "
          f"{off_res['share']:.1f}%")
    print(f"  С пингами  : холодных {on_res['cold']}/{on_res['total']} = "
          f"{on_res['share']:.1f}%   (пингов {on_res['pings']})")
    print(f"  промахи пингов: породы «сломан» {on_res['miss_broken']}, "
          f"законных (запись умерла за ночь) {on_res['miss_legit']}")
    print(f"  ⚠️ утренних пересозданий записи: {on_res['night_writes']} за "
          f"{DAYS} сут — это ЗАПИСЬ кэша по ставке 1h, см. отчёт")
    print(f"  теги пингов: {on_res['ping_tags']}")
    print()

    checks = [
        ("доля холодных С пингами ниже базлайна Г0 22.4%",
         on_res["share"] < BASELINE_COLD_SHARE),
        ("доля холодных С пингами ниже, чем БЕЗ них",
         on_res["share"] < off_res["share"]),
        ("плечо БЕЗ пингов не выродилось (холодные там ЕСТЬ)",
         off_res["cold"] > 0),
        ("пинги вообще уходили",
         on_res["pings"] > 0),
        ("промахов породы «префикс сломан» нет",
         on_res["miss_broken"] == 0),
        ("пинги писались ТОЛЬКО под keepalive_*",
         all(t.startswith(ka.KEEPALIVE_TAG_PREFIX)
             for t in on_res["ping_tags"])),
        ("оба боевых префикса грелись",
         len(on_res["ping_tags"]) == 2),
    ]
    bad = 0
    for name, ok in checks:
        print(f"  {'ok  ' if ok else 'ПЛОХО'} {name}")
        bad += 0 if ok else 1
    print()
    if bad:
        print(f"Г4-а НЕ ПРОЙДЕН: провалов {bad}")
        return 1
    print("Г4-а ПРОЙДЕН")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
