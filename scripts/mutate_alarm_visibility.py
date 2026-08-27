"""Мутационный гейт арки «тревога, которую нельзя не заметить» (§5.1–§5.3).

Мишени — РЕШЕНИЯ спеки, а не слова кода: раздельность термов и отсутствие
агрегата рядом; имя провода в красном вердикте; след ДО провода и исход ПОСЛЕ;
различимость успеха и провала на диске; fail-closed в скрипте пинга; ярлык у
каждого ключа, который цикл способен выставить.

Сторожей писал ДРУГОЙ автор, от текста спеки, реализации не видя. Поэтому гейт
проверяет не код, а ИХ: ломаем решение и требуем, чтобы покраснел named сторож.

Правки пишутся БАЙТАМИ. `Path.write_text` под Windows перевёл бы LF-файл в CRLF
целиком, и «мутация» превратилась бы в правку всего файла — так уже гасли две
мутации молча. По той же причине фрагменты выбраны ASCII-ТОЛЬКО: cp1251 не знает
части кириллических байт, и пойманная мутация печаталась бы слепой.

Прогон: python scripts/mutate_alarm_visibility.py  (в worktree, дерево чистое)
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

# Уникальный mtime на каждую запись: Python признаёт кэш байткода актуальным по
# паре (mtime в целых секундах, размер), и две мутации одного размера в одну
# секунду неотличимы — вторая исполнилась бы байткодом первой (DEV-26).
_MTIME_BASE = 2_000_000_000
_mtime_seq = count()

OW = "scripts/ops_watchdog.py"
PS = "scripts/healthchecks_ping.ps1"
R = "tests/test_ops_watchdog_reachability.py"
T = "tests/test_ops_watchdog_alert_trace.py"
H = "tests/test_healthchecks_ping_reachability.py"
L = "tests/test_ops_watchdog_label_coverage.py"

MUTATIONS = [
    # ── §5.1: раздельность термов ─────────────────────────────────────────
    ("три провода схлопнуты в ОДИН агрегат", OW,
     [(b'            probes[REACH_PROBE_PREFIX + str(name)] = probe_reachability(entry)',
       b'            probes["reachability"] = probe_reachability(entry)')],
     R + "::test_one_dead_wire_reddens_itself_and_only_itself[tg_api]"),

    ("агрегат ПОЯВИЛСЯ РЯДОМ с тремя термами", OW,
     [(b'    if reachability_snapshot:\n        for name, entry in',
       b'    if reachability_snapshot:\n        probes["network"] = {"ok": True, "detail": ""}\n        for name, entry in')],
     R + "::test_there_is_no_aggregate_verdict_next_to_the_three"),

    ("красный вердикт перестал называть свой провод", OW,
     [(b'            "detail": "%s: %s" % (entry.get("url", "?"),',
       b'            "detail": "%s: %s" % ("",')],
     R + "::test_the_red_verdict_names_its_own_wire"),

    ("живой провод объявлен мёртвым (зелёное перестало быть тихим)", OW,
     [(b'    if entry.get("ok"):\n        return {"ok": True,',
       b'    if not entry.get("ok"):\n        return {"ok": True,')],
     R + "::test_all_wires_green_is_silent"),

    # ── §5.3: след тревоги на диске ───────────────────────────────────────
    ("след ПЕРЕД проводом больше не пишется", OW,
     [(b'    _trace_append({"ts": now, "kind": kind, "stage": "attempt", "text": head},\n                  path=path)',
       b'    pass')],
     T + "::test_the_attempt_record_is_complete_before_the_result_exists"),

    ("исход отправки не записывается вовсе", OW,
     [(b'    _trace_append(record, path=path)\n    return ok',
       b'    return ok')],
     T + "::test_one_alert_leaves_two_records_attempt_and_result"),

    ("причина провала выброшена", OW,
     [(b'    if error:\n        record["error"] = error',
       b'    if False:\n        record["error"] = error')],
     T + "::test_the_failure_reason_is_written_down"),

    ("успех и провал стали неразличимы на диске", OW,
     [(b'record = {"ts": time.time(), "kind": kind, "stage": "result", "ok": ok}',
       b'record = {"ts": time.time(), "kind": kind, "stage": "result", "ok": True}')],
     T + "::test_success_and_failure_are_distinguishable_on_disk"),

    ("немота из-за отсутствия токена не оставляет следа", OW,
     [(b'    sender = sender or _send_tg_raw',
       b'    if not _bot_token():\n        return False\n    sender = sender or _send_tg_raw')],
     T + "::test_a_missing_token_is_also_recorded"),

    # ── §5.2: fail-closed в скрипте пинга ─────────────────────────────────
    ("отсутствие вердикта перестало быть проблемой", PS,
     [(b'if (-not (Test-Path $reachFile)) {', b'if ($false) {')],
     H + "::test_missing_verdict_is_a_problem_not_a_shrug"),

    ("протухший вердикт считается свежим", PS,
     [(b'      if ($reachAge -gt $FreshSeconds) {', b'      if ($false) {')],
     H + "::test_stale_verdict_is_a_problem_even_when_it_was_green"),

    ("мёртвый провод не попадает в жалобу", PS,
     [(b'        if (-not $wireOk) {', b'        if ($false) {')],
     H + "::test_a_single_dead_wire_fails_closed_and_names_it[tg_api]"),

    # ── ярлыки: сырой ключ вместо фразы ───────────────────────────────────
    ("у ключа связи пропал человеческий ярлык", OW,
     [(b'    "reach:tg_api": ', b'    "reach:tg_api_OPECHATKA": ')],
     L + "::test_every_static_key_the_cycle_can_emit_has_a_human_label"),
]


def write_mutant(path: Path, text) -> None:
    """Записать мутанта с УНИКАЛЬНЫМ mtime.

    Пишем всегда `write_bytes`: `write_text` перевёл бы LF-файл в CRLF целиком.
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
         "-p", "no:cacheprovider", "-p", "no:randomly"],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env)
    out = (p.stdout or "") + (p.stderr or "")
    tail = out.strip().splitlines()[-1] if out.strip() else "(пусто)"
    return (p.returncode == 1 and "failed" in out,
            "rc=%d | %s" % (p.returncode, tail[:140]))


def revert(rel: str) -> None:
    subprocess.run(["git", "checkout", "--", rel], cwd=ROOT, check=True)


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
        missing = [old for old, _new in edits if old not in mutated]
        if missing:
            # Не применившаяся мутация — НЕ «ok»: она не проверила ничего.
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
            revert(rel)
        after = path.read_bytes()
        if after != original:
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
    print("Все %d мутаций пойманы, файл возвращён побайтово." % len(MUTATIONS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
