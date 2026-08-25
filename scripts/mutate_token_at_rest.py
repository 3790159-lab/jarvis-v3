"""Мутационный гейт DEV-74 §5: ломаем пробу «токен в данных» и требуем КРАСНОГО.

Мишени — РЕШЕНИЯ пробы, а не её слова: красное на найденном токене, красное на
непросмотренном, раздельный счёт двух причин пропуска, названный охват на
зелёном, регистрация в цикле и её отсутствие при пустом снимке.

Правки пишутся БАЙТАМИ. `Path.write_text` под Windows переводит `\n` в `\r\n`,
файл LF-овый целиком, и «мутация» превратилась бы в правку 2967 строк —
[[jarvis-write-text-converts-newlines]]: так уже гасли две мутации молча.

Прогон: python scripts/mutate_token_at_rest.py  (в worktree, дерево чистое)
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
T = "tests/test_ops_watchdog_token_at_rest.py"

MUTATIONS = [
    ("найденный токен больше не красный", OW,
     [(b'    if offenders:\n        shown = ", ".join(offenders[:5])',
       b'    if False:\n        shown = ", ".join(offenders[:5])')],
     f"{T}::test_probe_red_names_the_files"),

    ("«не смогли посмотреть» выдаётся за «посмотрели, чисто»", OW,
     [(b'    error = snapshot.get("error")\n    if error:',
       b'    error = snapshot.get("error")\n    if False:')],
     f"{T}::test_probe_unreadable_is_red_not_green"),

    ("несобранный снимок молча становится пустым", OW,
     [(b'    if not isinstance(snapshot, dict):\n        return {"ok": False, "reason": "unreadable",\n                "detail": "\xd1\x81\xd0\xbd\xd0\xb8\xd0\xbc\xd0\xbe\xd0\xba \xd1\x82\xd0\xbe\xd0\xba\xd0\xb5\xd0\xbd\xd0\xbe\xd0\xb2 \xd0\xbd\xd0\xb5 \xd1\x81\xd0\xbe\xd0\xb1\xd1\x80\xd0\xb0\xd0\xbd"}',
       b'    if not isinstance(snapshot, dict):\n        snapshot = {}')],
     f"{T}::test_probe_refuses_a_missing_snapshot"),

    ("занятый файл снова считается «слишком большим» (одно число на две причины)", OW,
     [(b'                unreadable += 1\n                continue',
       b'                oversize += 1\n                continue')],
     f"{T}::test_oversize_and_unreadable_are_counted_apart"),

    ("исключённые каталоги перестали исключаться", OW,
     [(b'            if set(TOKEN_SCAN_SKIP_DIRS) & set(p.parts):\n                continue',
       b'            if False:\n                continue')],
     f"{T}::test_snapshot_skips_connect_where_secrets_are_legal"),

    ("порог размера игнорируется — пропуск не считается вовсе", OW,
     [(b'                if p.stat().st_size > TOKEN_SCAN_MAX_FILE_BYTES:\n                    oversize += 1\n                    continue',
       b'                if False:\n                    oversize += 1\n                    continue')],
     f"{T}::test_snapshot_counts_oversize_instead_of_hiding_it"),

    ("зелёное молчит про суженный охват", OW,
     [(b'    return {"ok": True, "detail": "\xd1\x82\xd0\xbe\xd0\xba\xd0\xb5\xd0\xbd\xd0\xbe\xd0\xb2 \xd0\xb2 \xd0\xb4\xd0\xb0\xd0\xbd\xd0\xbd\xd1\x8b\xd1\x85 \xd0\xbd\xd0\xb5\xd1\x82 (%s)" % tail}',
       b'    return {"ok": True, "detail": "\xd1\x82\xd0\xbe\xd0\xba\xd0\xb5\xd0\xbd\xd0\xbe\xd0\xb2 \xd0\xb2 \xd0\xb4\xd0\xb0\xd0\xbd\xd0\xbd\xd1\x8b\xd1\x85 \xd0\xbd\xd0\xb5\xd1\x82"}')],
     f"{T}::test_probe_green_still_says_what_was_not_looked_at"),

    ("регекс перестал узнавать токен", OW,
     [(b'TOKEN_AT_REST_RE = re.compile(rb"bot\\d{6,}:")',
       b'TOKEN_AT_REST_RE = re.compile(rb"bot\\d{40,}:")')],
     f"{T}::test_snapshot_finds_the_token_and_names_the_file"),

    ("в улику кладётся СОДЕРЖИМОЕ файла, а не путь (второй канал утечки)", OW,
     [(b'                    offenders.append(str(p.relative_to(live_tree)))',
       b'                    offenders.append(blob.decode("utf-8", "replace"))')],
     f"{T}::test_snapshot_finds_the_token_and_names_the_file"),

    ("проба выпала из цикла", OW,
     [(b'    if token_at_rest_snapshot:\n        probes["token_at_rest"] = probe_token_at_rest(token_at_rest_snapshot)',
       b'    if False:\n        probes["token_at_rest"] = probe_token_at_rest(token_at_rest_snapshot)')],
     f"{T}::test_probe_is_registered_in_probe_all"),

    ("пустой снимок всё равно даёт вердикт о том, чего не мерили", OW,
     [(b'    if token_at_rest_snapshot:\n        probes["token_at_rest"] = probe_token_at_rest(token_at_rest_snapshot)',
       b'    if True:\n        probes["token_at_rest"] = probe_token_at_rest(token_at_rest_snapshot)')],
     f"{T}::test_no_snapshot_means_no_probe_at_all"),
]


def write_mutant(path: Path, text) -> None:
    """Запись мутанта. Контракт мета-сторожа DEV-26: (путь, ТЕКСТ).

    Принимает и `str`, и `bytes`: гейт работает байтами (мутации заданы
    байтовыми фрагментами), а мета-сторож зовёт с `str` — и зовёт по делу,
    иначе защита от чужого байткода осталась бы НЕИЗМЕРЕННОЙ.

    Пишем всегда `write_bytes`: `write_text` перевёл бы LF-файл в CRLF
    целиком ([[jarvis-write-text-converts-newlines]]).
    """
    blob = text if isinstance(text, (bytes, bytearray)) else str(text).encode("utf-8")
    path.write_bytes(blob)
    stamp = _MTIME_BASE + next(_mtime_seq)
    os.utime(path, (stamp, stamp))


def run(test: str) -> tuple[bool, str]:
    """(поймана ли мутация, чем ответил pytest).

    КРАСНОЕ — РОВНО `rc 1` плюс `failed`: сломавшая СБОР мутация отвечает
    rc 2/4/5, и критерий «не 0 значит покраснел» принял бы её за пойманную.

    encoding явный: cp1251 не знает байта `0x98` из «И», и пойманная мутация
    печаталась бы слепой ([[jarvis-mutation-gate-lies-third-way-cp1251]]).
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
            print(f"[!] МУТАЦИЯ НЕ ПРИМЕНИЛАСЬ: {name} — фрагмент не найден")
            blind.append((name, "фрагмент не найден"))
            continue
        for old, new in edits:
            mutated = mutated.replace(old, new, 1)
        if mutated == original:
            print(f"[!] МУТАЦИЯ НИЧЕГО НЕ ИЗМЕНИЛА: {name}")
            blind.append((name, "файл не изменился"))
            continue
        write_mutant(path, mutated)
        try:
            caught, why = run(test)
        finally:
            revert(rel)
        after = path.read_bytes()
        if after != original:
            raise SystemExit(f"ОТКАТ НЕ ВЕРНУЛ ФАЙЛ ПОБАЙТОВО: {rel}")
        if not caught:
            print(f"[СЛЕП] {name}\n        {test}\n        {why}")
            blind.append((name, test))
        else:
            print(f"[ok]   {name} -> сторож покраснел")
    print()
    if blind:
        print(f"СЛЕПЫХ СТОРОЖЕЙ: {len(blind)} из {len(MUTATIONS)}")
        return 1
    print(f"Все {len(MUTATIONS)} мутаций пойманы, файл возвращён побайтово.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
