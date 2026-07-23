# -*- coding: utf-8 -*-
"""Карантин тестового мусора в боевом леджере расходов.

Тесты, забывшие выставить ``JARVIS_COST_FILE``, годами писали в
``state/cost_tracking.json`` (закрыто гардом в ``cost_tracker._state_file()``).
Осадок остался и завышает суммы, на которые владелец смотрит, решая, сколько
тратит.

Записи НЕ УДАЛЯЮТСЯ. Они переносятся в секцию ``quarantine`` того же файла с
причиной и датой — так видно, что именно вычтено, и решение обратимо. Удалить
молча значило бы заменить одну неверную цифру другой, столь же непроверяемой.

    python -m app.services.audit.cost_ledger_clean            # отчёт, ничего не меняет
    python -m app.services.audit.cost_ledger_clean --apply    # перенести в карантин
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

# Признаки тестовой записи. Оба обязательны — «нет username» само по себе
# бывает у реального пользователя, не назвавшего себя, а короткий числовой id
# сам по себе теоретически возможен у настоящего Telegram-аккаунта.
_SUSPECT_IDS = {"42", "7", "111", "555", "999", "None"}
_KNOWN_TEST_USERNAMES = {"tester", "u"}


def classify(users: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """(подозрительные, чистые). Чистая функция — решение отделено от записи."""
    suspect, clean = [], []
    for uid, rec in users.items():
        uname = rec.get("username")
        is_suspect = uid in _SUSPECT_IDS or (uname in _KNOWN_TEST_USERNAMES)
        (suspect if is_suspect else clean).append(uid)
    return sorted(suspect, key=lambda x: -float(users[x].get("all_time", 0))), sorted(clean)


def build_report(state: Dict[str, Any]) -> str:
    users = state.get("users", {})
    suspect, clean = classify(users)
    lines = ["=== БОЕВОЙ ЛЕДЖЕР: что предлагается вычесть ===", ""]

    total_all = sum(float(users[u].get("all_time", 0)) for u in users)
    total_suspect = sum(float(users[u].get("all_time", 0)) for u in suspect)

    lines.append("ПОДОЗРИТЕЛЬНЫЕ (тестовые фикстуры):")
    for uid in suspect:
        rec = users[uid]
        lines.append(
            f"  uid={uid:<8} username={str(rec.get('username')):<14} "
            f"all_time=${float(rec.get('all_time', 0)):>8.2f}  "
            f"last_seen={str(rec.get('last_seen'))[:10]}")
    if not suspect:
        lines.append("  (нет)")

    lines += ["", "ЧИСТЫЕ (остаются в учёте):"]
    for uid in clean:
        rec = users[uid]
        lines.append(
            f"  uid={uid:<8} username={str(rec.get('username')):<14} "
            f"all_time=${float(rec.get('all_time', 0)):>8.2f}")

    lines += [
        "",
        f"ИТОГО в файле:        ${total_all:>9.2f}",
        f"вычитается (карантин): ${total_suspect:>9.2f}",
        f"остаётся реальным:     ${total_all - total_suspect:>9.2f}",
        "",
        "⚠️ Это суммы ОЦЕНОК (est_usd), а не счета провайдеров. Реальные деньги —",
        "   только в кабинетах Replicate / Anthropic / fal / WaveSpeed.",
    ]
    return "\n".join(lines)


def apply_quarantine(state: Dict[str, Any], *, now: str) -> Dict[str, Any]:
    """Переносит подозрительные записи в ``quarantine``. Идемпотентно."""
    users = state.get("users", {})
    suspect, _ = classify(users)
    if not suspect:
        return state
    quarantine = state.setdefault("quarantine", {})
    for uid in suspect:
        rec = users.pop(uid)
        rec["_quarantined_at"] = now
        rec["_quarantined_reason"] = (
            "тестовая фикстура писала в боевой леджер до гарда в "
            "cost_tracker._state_file() (2026-07-24)")
        quarantine[uid] = rec
    return state


def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Карантин тестовых записей в леджере")
    p.add_argument("--file", default=str(Path("state") / "cost_tracking.json"))
    p.add_argument("--apply", action="store_true",
                   help="перенести в карантин (по умолчанию только отчёт)")
    args = p.parse_args(argv)

    path = Path(args.file)
    if not path.exists():
        print(f"леджер не найден: {path}")
        return 1
    state = json.loads(path.read_text(encoding="utf-8"))

    print(build_report(state))

    if not args.apply:
        print("\n(это только отчёт; чтобы применить — --apply)")
        return 0

    backup = path.with_suffix(path.suffix + f".bak-{datetime.now():%Y%m%d-%H%M%S}")
    shutil.copy2(path, backup)
    state = apply_quarantine(state, now=datetime.now().isoformat(timespec="seconds"))
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    print(f"\n✅ применено. Бэкап: {backup}")
    print("Записи НЕ удалены — они в секции 'quarantine' того же файла.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
