# -*- coding: utf-8 -*-
"""Кого ПЕРЕОТВЕТИТ catch-up, если поднять раннера прямо сейчас.

Повод — живой случай 17.08. Рестарт Ярины после мержа заставил catch-up
обработать сообщение 13 ч 49 мин давности: диалог был ЭСКАЛИРОВАН, бот молчал
не потому, что не успел, а потому что передал ведение человеку. Catch-up этой
разницы не знает, ответил дважды и поднял новую карточку — поверх человека,
который уже вёл клиента.

Пока catch-up не научится спрашивать состояние контакта, замер обязан идти
ПЕРЕД каждым запуском, и жить он должен в скрипте, а не в чьей-то памяти.

## Чем этот замер является и чем НЕ является

Настоящий триггер catch-up — НЕПРОЧИТАННЫЕ диалоги в Telegram (см.
`telethon_run.collect_missed`), а их снаружи, не поднимая сессию, не увидеть.
Здесь используется доступный офлайн-признак: последнее слово в диалоге за
КЛИЕНТОМ. Совпадение с настоящим триггером неточное в обе стороны, и об этом
надо помнить:

* бот мог ответить в Telegram, но не записать в БД — тогда мы поднимем тревогу
  зря (безопасная сторона: лишний вопрос человеку);
* сообщение могло прийти на аккаунт от неизвестного, не попасть в БД вовсе —
  тогда мы его не увидим (опасная сторона, и она названа честно).

Возрастной порог берётся ИЗ САМОГО catch-up (`CATCHUP_MAX_AGE_SECONDS`), а не
переписывается сюда: два числа на одну вещь однажды разъедутся, и меньшее
погасит большее молча.

Коды выхода: 0 — переотвечать нечего; 1 — есть кого переответить (список
напечатан); 2 — замер НЕ СОСТОЯЛСЯ (нет БД, нет клиента, битая БД). Молчание
инструмента не имеет права читаться как «чисто».
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chatter.telethon_run import CATCHUP_MAX_AGE_SECONDS  # noqa: E402

RC_CLEAN = 0
RC_RISK = 1
RC_NOT_RUN = 2

# Состояния, в которых молчание бота — РЕШЕНИЕ, а не пропуск. Ответ поверх
# такого молчания это не «лишняя реплика», а вмешательство в диалог, который
# ведёт человек.
SILENT_BY_DECISION = ("escalated",)


class RadiusError(Exception):
    """Замер не состоялся. Отдельный тип: «не смогли посмотреть» обязано
    отличаться от «посмотрели, чисто»."""


def dialogs_at_risk(db_path, *, now: float | None = None,
                    max_age_seconds: int = CATCHUP_MAX_AGE_SECONDS) -> list[dict]:
    """Диалоги, последнее слово в которых за клиентом и они свежее порога.

    Возвращает по одному словарю на диалог: contact_id, возраст последнего
    сообщения, его текст (обрезанный) и признак `deliberate_silence` — молчал
    ли бот по решению.
    """
    now = time.time() if now is None else now
    path = Path(db_path)
    if not path.is_file():
        raise RadiusError(f"БД клиента не найдена: {path}")
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        rows = con.execute(
            """
            select m.contact_id, m.role, m.ts, m.text,
                   c.state, c.paused, c.human_took_over
              from messages m
              join (select contact_id, max(ts) as ts
                      from messages group by contact_id) last
                on m.contact_id = last.contact_id and m.ts = last.ts
              left join contacts c on c.contact_id = m.contact_id
            """
        ).fetchall()
    except sqlite3.Error as exc:
        raise RadiusError(f"БД не читается ({path}): {exc}") from exc
    finally:
        try:
            con.close()
        except Exception:  # noqa: BLE001 - соединение могло не открыться
            pass

    out: list[dict] = []
    for contact_id, role, ts, text, state, paused, took_over in rows:
        if role != "user":
            continue
        age = now - float(ts or 0)
        if age > max_age_seconds:
            # Старше порога catch-up не тронет — и мы не тревожим зря.
            continue
        out.append({
            "contact_id": contact_id,
            "age_hours": round(age / 3600, 1),
            "text": (text or "").strip()[:120],
            "deliberate_silence": bool(
                (state or "") in SILENT_BY_DECISION or paused or took_over),
            "state": state,
        })
    return sorted(out, key=lambda r: (-int(r["deliberate_silence"]), r["age_hours"]))


def db_for(root: Path, slug: str) -> Path:
    """Путь к БД клиента из реестра — единственного места, где он объявлен."""
    import yaml  # локально: у ops-скрипта нет права утащить чужой импорт в старт

    registry = root / "chatter" / "clients" / "registry.yaml"
    if not registry.is_file():
        raise RadiusError(f"реестр не найден: {registry}")
    try:
        data = yaml.safe_load(registry.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise RadiusError(f"реестр не разбирается: {exc}") from exc
    clients = (data or {}).get("clients") or {}
    entry = clients.get(slug)
    if entry is None:
        raise RadiusError(
            f"клиента '{slug}' нет в реестре — замерять нечего "
            f"(есть: {', '.join(sorted(clients)) or 'ни одного'})")
    db = (entry or {}).get("db")
    if not db:
        raise RadiusError(f"у клиента '{slug}' в реестре не указан db")
    return root / db


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--slug", required=True)
    ap.add_argument("--root", default=r"C:\jarvis")
    ap.add_argument("--json", action="store_true", help="машинный вывод")
    args = ap.parse_args(argv)

    try:
        db = db_for(Path(args.root), args.slug)
        if not db.is_file():
            # Клиент ещё ни разу не работал — переотвечать нечего, и это
            # ЗАКОННОЕ «чисто», а не отказ. Иначе первый же онбординг упёрся
            # бы в замер, который охраняет историю, которой нет.
            print(f"[radius] {args.slug}: БД ещё нет ({db}) — клиент не работал, "
                  f"переотвечать нечего")
            return RC_CLEAN
        rows = dialogs_at_risk(db)
    except RadiusError as exc:
        print(f"[radius] НЕ СОСТОЯЛСЯ: {exc}")
        return RC_NOT_RUN

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    if not rows:
        print(f"[radius] {args.slug}: переотвечать нечего "
              f"(диалогов с последним словом клиента свежее "
              f"{CATCHUP_MAX_AGE_SECONDS // 3600} ч — ноль)")
        return RC_CLEAN

    print(f"[radius] {args.slug}: catch-up ответит на {len(rows)} диалог(ов) "
          f"при следующем подъёме")
    for r in rows:
        mark = "🔴 бот молчал ПО РЕШЕНИЮ" if r["deliberate_silence"] else "   ждёт ответа"
        print(f"  {mark}  {r['contact_id']}  {r['age_hours']} ч  state={r['state']}")
        print(f"      «{r['text']}»")
    return RC_RISK


if __name__ == "__main__":
    raise SystemExit(main())
