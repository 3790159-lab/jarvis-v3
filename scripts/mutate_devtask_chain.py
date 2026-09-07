# -*- coding: utf-8 -*-
"""Мутационный гейт цепочки DevTask (спека 2026-09-07-devtask-chain.md §8).

Мишени — РЕШЕНИЯ спеки, а не слова кода:
  1  база шага N — ветка предыдущего, а не prod_head (сердце варианта C);
  2  дыра в плане (у предыдущего шага нет задачи) — ОТКАЗ, а не молчаливый
     откат к prod_head;
  3  продвижение только после awaiting_review, а не из любого состояния;
  4  остановленная цепочка не выдаёт следующий шаг;
  5  удаление в дифе останавливает ДАЖЕ при зелёном гейте;
  6  удаление имеет ПРИОРИТЕТ над другими причинами;
  7  красный гейт останавливает;
  8  rate-limit останавливает (под подпиской это главный дефицит);
  9  таймаут шага останавливает;
  10 откат идёт от последнего шага к первому;
  11 откат отказывается, если ветка уже в транке;
  12 потолок в 10 шагов держится;
  13 дописывание шага в цепочку запрещено.

Мишени 2, 4, 6, 11 — ВСТРЕЧНЫЕ: они ломают не действие, а его ГРАНИЦУ. Без них
«продвигай всегда» и «снимай всё подряд» прошли бы позитивные сторожа.

Прогон: python scripts/mutate_devtask_chain.py  (в worktree, дерево чистое)
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

_MTIME_BASE = 2_000_000_000
_mtime_seq = count()

C = "app/services/devtask/chain.py"
T = "tests/test_devtask_chain.py"

MUTATIONS = [
    # ── 1: сердце варианта C — база берётся у предыдущего шага ───────────
    ("база шага N снова prod_head — цепочка теряет код предыдущего шага", C,
     [(b"    if step_no <= 1:\n        return prod_head",
       b"    if True:\n        return prod_head")],
     T + "::test_second_step_starts_from_the_previous_branch"),

    # ── 2 (встречная): дыра в плане проглатывается молча ─────────────────
    ("дыра в плане молчит: у предыдущего шага нет задачи, берём prod_head", C,
     [(b'    if not prev.get("task_id"):',
       b'    if False:')],
     T + "::test_base_for_step_refuses_when_previous_has_no_task"),

    # ── 3: продвижение только после awaiting_review ──────────────────────
    #
    # Первая редакция мишени ломала проверку предшественника внутри ветки
    # PENDING — и гейт показал, что та ветка НЕДОСТИЖИМА (мой мёртвый код,
    # снят). Настоящее решение живёт в нижней проверке: недоделанный шаг
    # обрывает поиск.
    ("продвижение из любого состояния — следующий шаг стартует поверх идущего", C,
     [(b'        if step["status"] not in _STEP_DONE_STATES:\n            return None',
       b'        if False:\n            return None')],
     T + "::test_next_step_is_none_while_current_is_running"),

    # ── 4 (встречная): остановленная цепочка продолжает ехать ────────────
    ("остановленная цепочка выдаёт следующий шаг", C,
     [(b'    if chain.get("status") != CHAIN_RUNNING:\n        return None',
       b'    if False:\n        return None')],
     T + "::test_stopped_chain_yields_no_next_step"),

    # ── 5: удаление останавливает даже по зелёному ───────────────────────
    ("удаление в дифе перестало останавливать цепочку", C,
     [(b'    if verdict.get("has_deletions"):\n        return STOP_DELETION',
       b'    if False:\n        return STOP_DELETION')],
     T + "::test_deletion_stops_even_on_a_green_gate"),

    # ── 6 (встречная): приоритет причины удаления ────────────────────────
    ("удаление потеряло приоритет — владельцу назовут другую причину", C,
     [(b'    if verdict.get("has_deletions"):\n        return STOP_DELETION\n    if not verdict.get("worktree_ok", True):',
       b'    if not verdict.get("worktree_ok", True):')],
     T + "::test_deletion_wins_over_other_reasons"),

    # ── 7: красный гейт ──────────────────────────────────────────────────
    ("красный гейт больше не останавливает цепочку", C,
     [(b'    if not verdict.get("gate_ok", True):\n        return STOP_GATE_RED',
       b'    if False:\n        return STOP_GATE_RED')],
     T + "::test_red_gate_stops"),

    # ── 8: rate-limit ────────────────────────────────────────────────────
    ("rate-limit не останавливает — цепочка бьётся в стену лимита плана", C,
     [(b'    if verdict.get("rate_limited"):\n        return STOP_RATE_LIMIT',
       b'    if False:\n        return STOP_RATE_LIMIT')],
     T + "::test_rate_limit_stops"),

    # ── 9: таймаут шага ──────────────────────────────────────────────────
    ("таймаут шага снят — зависший шаг держит цепочку вечно", C,
     [(b'    if float(verdict.get("step_elapsed_s") or 0) > STEP_TIMEOUT_S:',
       b'    if False:')],
     T + "::test_step_timeout_stops"),

    # ── 10: порядок отката ───────────────────────────────────────────────
    ("откат идёт от первого шага к последнему — останутся висящие ветки", C,
     [(b"        for s in reversed(steps)",
       b"        for s in steps")],
     T + "::test_rollback_goes_from_the_last_step_to_the_first"),

    # ── 11 (встречная): откат смерженной ветки ───────────────────────────
    ("откат сносит ветку, уже уехавшую в транк", C,
     [(b"        if is_merged(branch):",
       b"        if False:")],
     T + "::test_rollback_refuses_when_a_branch_is_already_in_trunk"),

    # ── 12: потолок шагов ────────────────────────────────────────────────
    ("потолок в 10 шагов снят", C,
     [(b"        if len(descs) > MAX_STEPS:",
       b"        if False:")],
     T + "::test_chain_longer_than_the_cap_is_refused"),

    # ── 13: дописывание в цепочку ────────────────────────────────────────
    #
    # Фрагмент кириллический, поэтому кодируется явно: `b"..."` с не-ASCII —
    # SyntaxError, и гейт упал бы на разборе, не проверив ни одной мишени.
    ("в идущую цепочку снова можно дописывать шаги", C,
     [("        raise ChainClosedError(".encode("utf-8"),
       "        return self._require(chain_id)  # мутация\n        raise ChainClosedError(".encode("utf-8"))],
     T + "::test_appending_a_step_to_a_running_chain_is_refused"),
]


def write_mutant(path: Path, text) -> None:
    """Запись мутанта со СВОЕЙ отметкой времени.

    Одинаковый mtime у двух записей подряд оставляет питону старый .pyc, и
    мутация не доезжает до прогона: гейт зеленеет, не проверив ничего.
    """
    blob = text if isinstance(text, (bytes, bytearray)) else str(text).encode("utf-8")
    path.write_bytes(blob)
    stamp = _MTIME_BASE + next(_mtime_seq)
    os.utime(path, (stamp, stamp))


def run(test: str) -> "tuple[bool, str]":
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
    print()
    if blind:
        print("СЛЕПЫХ СТОРОЖЕЙ: %d из %d" % (len(blind), len(MUTATIONS)))
        return 1
    print("Все %d мутаций пойманы, файлы возвращены побайтово." % len(MUTATIONS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
