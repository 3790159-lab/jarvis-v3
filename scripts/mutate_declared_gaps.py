"""Мутационный гейт DEV-77: ломаем сверку ожидания с диском, требуем КРАСНОГО.

Мишени — четыре состояния §5 спеки и умолчание неизвестности. Отдельно
мутируется ОБЕ половины четвёртого состояния: «кричит, но не везёт» и «везёт,
но молчит» — реализация, прошедшая только одну, теряет либо копию платёжных
данных, либо сам сигнал.

Правки пишутся БАЙТАМИ (LF-файл, `write_text` перевёл бы его в CRLF целиком —
[[jarvis-write-text-converts-newlines]]).

Прогон: python scripts/mutate_declared_gaps.py  (в worktree, дерево чистое)
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

SB = "app/services/state_backup.py"
T = "tests/test_backup_declared_gaps.py"

MUTATIONS = [
    ("предикат перевёрнут: ждём у тех, у кого платежей нет", SB,
     [(b'    if not isinstance(flag, bool):\n        return True\n    return flag',
       b'    if not isinstance(flag, bool):\n        return True\n    return not flag')],
     f"{T}::test_payments_on_means_the_file_is_expected"),

    ("неизвестность читается как «файла не ждём»", SB,
     [(b'    except (OSError, UnicodeDecodeError, yaml.YAMLError):\n        return True',
       b'    except (OSError, UnicodeDecodeError, yaml.YAMLError):\n        return False')],
     f"{T}::test_unknown_is_read_as_expected_not_as_absent"),

    ("строка \"false\" снова считается тумблером", SB,
     [(b'    if not isinstance(flag, bool):\n        return True',
       b'    if not isinstance(flag, bool):\n        return bool(flag)')],
     f"{T}::test_the_flag_must_be_a_real_boolean"),

    ("клиент без платежей опять числится пропажей (ежедневный rc=1)", SB,
     [(b'                if kind == "requisites" and not wants_requisites:\n                    continue\n                missing.append(rel)',
       b'                missing.append(rel)')],
     f"{T}::test_not_expected_and_absent_is_silent"),

    ("расхождение не записывается — файл едет МОЛЧА", SB,
     [(b'                undeclared.append(rel)\n', b'                pass\n')],
     f"{T}::test_not_expected_but_present_is_loud_AND_still_travels"),

    ("расхождение кричит, но файл НЕ везёт (потеря копии)", SB,
     [(b'                undeclared.append(rel)\n            key = normalize_path(str(path), root=root)',
       b'                undeclared.append(rel)\n                continue\n            key = normalize_path(str(path), root=root)')],
     f"{T}::test_not_expected_but_present_is_loud_AND_still_travels"),

    ("расхождение больше не портит вердикт (задача вернёт rc 0)", SB,
     [(b'        return not self.failed and not self.contradictions',
       b'        return not self.failed')],
     f"{T}::test_result_carries_the_contradiction_and_is_not_ok"),

    ("сводка молчит о расхождении", SB,
     [(b'    if result.contradictions:', b'    if False:')],
     f"{T}::test_summary_names_slug_path_and_what_to_do"),

    ("сверка перестала доезжать из отбора в набор", SB,
     [(b'            undeclared=tuple(undeclared),\n', b'            undeclared=(),\n')],
     f"{T}::test_not_expected_but_present_is_loud_AND_still_travels"),
]


def write_mutant(path: Path, text) -> None:
    """Запись мутанта. Контракт мета-сторожа DEV-26: (путь, ТЕКСТ).

    Принимает и `str`, и `bytes`: сам гейт работает байтами (мутации заданы
    байтовыми фрагментами), а мета-сторож зовёт с `str` — и зовёт по делу,
    иначе защита от чужого байткода осталась бы НЕИЗМЕРЕННОЙ, а гейт
    продолжал бы рапортовать «все мутации пойманы» (второй способ пройти
    мимо, замерен 20.08).

    Пишем в ЛЮБОМ случае `write_bytes`: `write_text` перевёл бы LF-файл в
    CRLF целиком, и мутация утонула бы в правке всего файла
    ([[jarvis-write-text-converts-newlines]]).
    """
    blob = text if isinstance(text, (bytes, bytearray)) else str(text).encode("utf-8")
    path.write_bytes(blob)
    stamp = _MTIME_BASE + next(_mtime_seq)
    os.utime(path, (stamp, stamp))


def run(test: str) -> tuple[bool, str]:
    """КРАСНОЕ — РОВНО rc 1 плюс `failed`: сломавший СБОР мутант даёт 2/4/5."""
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
        raise SystemExit("ОТКАЗ: дерево грязное — откат сотрёт эти правки.\n" + out)


def main() -> int:
    refuse_if_live_tree(ROOT)
    assert_clean()
    blind = []
    for name, rel, edits, test in MUTATIONS:
        path = ROOT / rel
        original = path.read_bytes()
        mutated = original
        if any(old not in mutated for old, _new in edits):
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
        if path.read_bytes() != original:
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
