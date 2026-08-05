# -*- coding: utf-8 -*-
"""Ш1: один теневой прогон яруса 2 — снимок, детекторы, запись в тень.

Склейка и только склейка: вся логика живёт в оттестированных модулях
(`autonomy_snapshot` — грязный сборщик, `autonomy_detectors` — чистые
функции, `autonomy_proposals` — хранилище). Здесь нет ни одного решения,
которое можно было бы принять неправильно молча.

Ничего не отправляет: `record()` физически умеет писать только `shadow`,
отправка — это Ш2 и отдельное решение владельца.

    python scripts/autonomy_shadow_run.py [--root C:\\jarvis] [--db state/autonomy.db]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import autonomy_detectors as det          # noqa: E402
from app.services import autonomy_proposals as ap           # noqa: E402
from app.services import autonomy_snapshot as snap          # noqa: E402

DETECTORS = (
    ("register-скрипт без таска", det.detect_register_scripts_without_task),
    ("пропавший повторяющийся триггер", det.detect_missing_trigger),
    ("два ненулевых кода подряд", det.detect_failing_task),
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(snap.ROOT),
                        help="корень наблюдаемого репозитория (только чтение)")
    parser.add_argument("--db", default="state/autonomy.db")
    parser.add_argument("--policy", default=None,
                        help="JSON {действие: уровень}; без него всё fail-closed 4")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    policy = json.loads(Path(args.policy).read_text(encoding="utf-8")) if args.policy else {}

    now = time.time()
    snapshot = snap.collect(root, now=now)
    print(f"снимок: корень {root}")
    print(f"  тасков фермы .......... {len(snapshot['tasks'])}")
    print(f"  register-скриптов ..... {len(snapshot['register_scripts'])}")
    print(f"  процессов ............. {len(snapshot['processes'])}")
    print(f"  git ................... {snapshot['git']}")
    age = snapshot["stamps"]["healthchecks_age_sec"]
    print(f"  healthchecks .......... {'нет метки' if age is None else f'{age/60:.1f} мин назад'}")

    conn = ap.connect(args.db)
    total_new = 0
    for title, detector in DETECTORS:
        found = detector(snapshot, policy)
        print(f"\n[{title}] предложений: {len(found)}")
        for proposal in found:
            outcome = ap.record(conn, proposal, now=now)
            total_new += outcome == "inserted"
            print(f"  {outcome:9} lvl{proposal.action_level}  {proposal.subject}")
            print(f"            {json.dumps(proposal.evidence, ensure_ascii=False)}")

    open_now = ap.list_shadow(conn)
    print(f"\nитого: новых {total_new}, открытых в тени {len(open_now)}; отправлено 0 (Ш1)")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
