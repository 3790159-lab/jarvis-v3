# -*- coding: utf-8 -*-
"""Мутационный гейт ШВА C-1: правило живёт в БОЕВОМ ходу, а не только в юните.

Гейт `mutate_sales_c1_lead_numbers.py` мутирует `guardrails.py`/`langdetect.py` —
то есть ПРАВИЛО. Шов же (`run.py` +37, `escalation.py` +5) не был заведён под
гейт вовсе: правило можно было оставить целым, а ход — обесточить, и ни один
мутант этого не показал бы.

Мишени — РЕШЕНИЯ шва, а не слова: откуда берутся числа лида (окно, роль,
текущее входящее, сам источник истории) и доезжают ли они до ОБОИХ
потребителей (детерминированная эскалация и редакция).

Прогон: python scripts/mutate_sales_c1_seam.py  (в worktree, дерево чистое)
"""
from __future__ import annotations

import os
import subprocess
import sys
from itertools import count
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gate_guard import refuse_if_live_tree   # DEV-31: гейт мутирует только worktree

ROOT = Path(__file__).resolve().parents[1]

# Уникальный mtime на каждую запись (DEV-26): байткод-кэш считает пару
# (mtime в целых секундах, размер) достаточной, и две мутации одного размера
# в одну секунду неотличимы — вторая исполнилась бы байткодом первой.
_MTIME_BASE = 2_000_000_000
_mtime_seq = count()

R = "chatter/run.py"
E = "chatter/core/escalation.py"

S = "tests/chatter/test_sales_c1_seam.py"
_WINDOW = S + "::test_lead_numbers_for_turn_takes_lead_messages_and_current_incoming"
_VERBATIM = S + "::test_retelling_lead_budget_reaches_the_lead_verbatim"
_OURPRICE = S + "::test_our_price_from_lead_number_still_does_not_reach_the_lead"

# Сторожа, дописанные после первого прогона (04.09, 4/7): мишени 1, 4 и 5 были
# слепы не потому, что код неверен, а потому, что случая не было вовсе.
_EDGE = S + "::test_lead_numbers_stop_at_the_edge_of_the_prompt_window"
_FROM_HISTORY = S + "::test_lead_number_from_stored_history_reaches_the_lead_verbatim"
_MIXED = S + "::test_mixed_reply_keeps_the_lead_number_and_cuts_only_the_invented_deadline"

MUTATIONS = [
    # ── откуда берутся числа лида ────────────────────────────────────────
    ("1. окно истории снято — обеспечиваем числом, которого бот не видел", R,
     [(b"    window = select_window(list(history or []),\n"
       b"                           budget_tokens=limits.history_budget_tokens,\n"
       b"                           max_messages=limits.history_max_messages)",
       b"    window = list(history or [])")],
     _EDGE),

    ("2. фильтр роли снят — числа БОТА начинают обеспечивать бота", R,
     [(b'    texts = [m.get("text") or "" for m in window if m.get("role") == "user"]',
       b'    texts = [m.get("text") or "" for m in window]')],
     _WINDOW),

    ("3. текущее входящее не учитывается — число, названное СЕЙЧАС, не в счёт", R,
     [(b'    texts.append(incoming_text or "")',
       b'    texts.append("")')],
     _WINDOW),

    ("4. источник истории подменён пустым — числа лида взять неоткуда", R,
     [(b"        store.history(contact_id), limits=cfg.settings.limits,",
       b"        [], limits=cfg.settings.limits,")],
     _FROM_HISTORY),

    # ── доезжают ли до ОБОИХ потребителей ────────────────────────────────
    ("5. редакция не получает числа лида — режет то, что назвал сам лид", R,
     [(b"            redacted = (_try_redact(deps, contact_id, reply=reply, now=now,\n"
       b"                                    lead_numbers=lead_nums)",
       b"            redacted = (_try_redact(deps, contact_id, reply=reply, now=now,\n"
       b"                                    lead_numbers=frozenset())")],
     _MIXED),

    ("6. детерминированная эскалация не получает числа лида — шов мёртв", R,
     [(b"        strict_knowledge=cfg.settings.strict_knowledge,\n"
       b"        lead_numbers=lead_nums)",
       b"        strict_knowledge=cfg.settings.strict_knowledge,\n"
       b"        lead_numbers=frozenset())")],
     _VERBATIM),

    ("7. escalation.py не пробрасывает числа лида в гардрейл", E,
     [(b'    if contains_unbacked_claim(reply or "", knowledge or "",\n'
       b"                               lead_numbers=lead_numbers):",
       b'    if contains_unbacked_claim(reply or "", knowledge or ""):')],
     _VERBATIM),
]


def write_mutant(path: Path, text) -> None:
    """Записать мутанта с УНИКАЛЬНЫМ mtime. Всегда `write_bytes`.

    Принимает И байты, И строку: мета-сторож на гейты зовёт этот метод строкой,
    и суженная до байтов сигнатура ломает ЗАМЕР гейта, а не сам гейт — то есть
    выглядит как «гейт не проверен», что в этом доме опаснее красного.
    """
    blob = text if isinstance(text, (bytes, bytearray)) else str(text).encode("utf-8")
    path.write_bytes(blob)
    stamp = _MTIME_BASE + next(_mtime_seq)
    os.utime(path, (stamp, stamp))


def run(test: str) -> tuple[bool, str]:
    """(поймана ли мутация, чем ответил pytest).

    КРАСНОЕ — РОВНО `rc 1` плюс `failed`: сломавшая СБОР мутация отвечает
    rc 2/4/5, и критерий «не 0 значит покраснел» принял бы её за пойманную.
    """
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    p = subprocess.run(
        [sys.executable, "-m", "pytest", test, "-q", "--no-header",
         "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env)
    out = (p.stdout or "") + (p.stderr or "")
    tail = out.strip().splitlines()[-1] if out.strip() else "(пусто)"
    return (p.returncode == 1 and "failed" in out,
            "rc=%d | %s" % (p.returncode, tail[:140]))


def revert(rel: str, original: bytes) -> None:
    path = ROOT / rel
    path.write_bytes(original)
    stamp = _MTIME_BASE + next(_mtime_seq)
    os.utime(path, (stamp, stamp))


def assert_clean() -> None:
    out = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                         cwd=ROOT, capture_output=True, text=True).stdout.strip()
    if out:
        raise SystemExit(
            "ОТКАЗ: рабочее дерево грязное — откат мутаций сотрёт эти правки.\n" + out)


def head_hash(rel: str) -> str:
    return subprocess.run(["git", "rev-parse", "HEAD:" + rel], cwd=ROOT,
                          capture_output=True, text=True).stdout.strip()[:12]


def disk_hash(rel: str) -> str:
    return subprocess.run(["git", "hash-object", rel], cwd=ROOT,
                          capture_output=True, text=True).stdout.strip()[:12]


def main() -> int:
    refuse_if_live_tree(ROOT)
    assert_clean()
    blind = []
    for name, rel, edits, test in MUTATIONS:
        path = ROOT / rel
        original = path.read_bytes()
        mutated = original
        edits = [(o, n) if o in mutated
                 else (o.replace(b"\n", b"\r\n"), n.replace(b"\n", b"\r\n"))
                 for o, n in edits]
        missing = [old for old, _new in edits if old not in mutated]
        if missing:
            print("[!] МУТАЦИЯ НЕ ПРИМЕНИЛАСЬ: %s — фрагмент не найден" % name)
            blind.append((name, "фрагмент не найден"))
            continue
        for old, new in edits:
            mutated = mutated.replace(old, new, 1)
        if mutated == original:
            print("[!] МУТАЦИЯ НИЧЕГО НЕ ИЗМЕНИЛА: %s" % name)
            blind.append((name, "файл не изменился"))
            continue
        write_mutant(path, mutated)
        try:
            caught, why = run(test)
        finally:
            revert(rel, original)
        if path.read_bytes() != original:
            raise SystemExit("ОТКАТ НЕ ВЕРНУЛ ФАЙЛ ПОБАЙТОВО: %s" % rel)
        if not caught:
            print("[СЛЕП] %s\n        %s\n        %s" % (name, test, why))
            blind.append((name, test))
        else:
            print("[ok]   %s -> сторож покраснел" % name)

    print("\nХэши после отката (mtime гейт НЕ восстанавливает — судить по хэшу):")
    for rel in (R, E):
        d, h = disk_hash(rel), head_hash(rel)
        print("  %-34s disk=%s head=%s %s" % (rel, d, h, "OK" if d == h else "РАЗОШЛИСЬ"))

    print()
    if blind:
        print("СЛЕПЫХ СТОРОЖЕЙ: %d из %d" % (len(blind), len(MUTATIONS)))
        return 1
    print("Все %d мутаций пойманы, файлы возвращены побайтово." % len(MUTATIONS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
