# -*- coding: utf-8 -*-
"""Мутационный гейт арки web-a «личность контакта» (спека §6).

Мишени — РЕШЕНИЯ спеки, а не слова кода: опознавать форму по ЧИСЛУ сегментов,
отказывать fail-closed вместо правдоподобного мусора, не течь голым
`ValueError` из недр `int()`, отдавать peer числом, — и обе ВСТРЕЧНЫЕ
половины: «fail-closed» не должен выродиться в «отказывать всегда», а уборка
восьми ручных разборов — причесать заодно живой хвостовой `rsplit`.

Сторожа писал ДРУГОЙ заход, от текста спеки. Гейт проверяет не код, а ИХ:
ломаем решение и требуем красного от НАЗВАННОГО сторожа (`файл::имя`), а не от
файла — прогон по файлу зеленел бы за счёт соседа, и карта «решение -> сторож»
врала бы при зелёном гейте.

Харнесс взят у гейта DEV-48 целиком, включая обе его поправки: откат по
СОХРАНЁННЫМ БАЙТАМ (git checkout врёт на autocrlf) и поиск фрагмента в обеих
формах концов строк. `write_mutant` принимает и строку, и байты — этого
требует мета-сторож `test_mutation_gate_bytecode`, и сужение сигнатуры до
байтов ломает ЗАМЕР гейта, а не сам гейт.

Прогон: python scripts/mutate_web_contact_ref.py  (в worktree, дерево чистое)
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

CR = "chatter/core/contact_ref.py"
RUN = "chatter/run.py"
TR = "chatter/telethon_run.py"

G = "tests/test_contact_ref.py"
GA = "tests/test_no_raw_contact_id_split.py"

MUTATIONS = [
    # ── §2.5 форма опознаётся по ЧИСЛУ сегментов, лишнее ОТВЕРГАЕТСЯ ──────
    ("проверка числа сегментов снята — три сегмента молча укорачиваются", CR,
     [(b"    if len(parts) != SEGMENTS_TODAY or not all(parts):",
       b"    if not all(parts):")],
     G + "::test_a_three_segment_form_is_rejected_not_silently_shortened[peer_of]"),

    # Самый правдоподобный способ «почти» выполнить требование: форма принята,
    # середина НЕ потеряна, обратимость цела — и ровно поэтому инвариант
    # обратимости эту мутацию не ловит, а сторож на три сегмента ловит.
    ("разбор с maxsplit=1 — форма принята, середина уехала в слуг", CR,
     [(b"    parts = contact_id.split(SEPARATOR)",
       b"    parts = contact_id.split(SEPARATOR, 1)")],
     G + "::test_a_three_segment_form_is_rejected_not_silently_shortened[slug_of]"),

    ("пустые сегменты снова проходят — одно двоеточие даёт две пустышки", CR,
     [(b"    if len(parts) != SEGMENTS_TODAY or not all(parts):",
       b"    if len(parts) != SEGMENTS_TODAY:")],
     G + "::test_empty_or_colonless_input_raises[peer_of-:]"),

    # ── §2.6 отказ обязан называть СЕБЯ и весь contact_id ─────────────────
    ("telegram_peer_of снова течёт голым ValueError из недр int()", CR,
     [(b"    except ValueError:", b"    except NotImplementedError:")],
     G + "::test_telegram_peer_of_refuses_by_itself_not_by_leaking_int_valueerror"),

    ("telegram_peer_of отдаёт строку вместо числа", CR,
     [(b"        return int(head)", b"        return head")],
     G + "::test_telegram_peer_of_returns_an_int_not_a_string"),

    # ВСТРЕЧНАЯ ПОЛОВИНА. Без неё «отказывать ВСЕГДА» прошло бы гейт: три
    # мишени на отказ остались бы зелёными, а модуль стал бы бесполезен.
    ("fail-closed выродился в «отказывать всегда»", CR,
     [(b"    return parts[0], parts[1]",
       b'    raise ContactRefError("gate")')],
     G + "::test_todays_two_segment_form_still_yields_the_bare_peer"),

    # ── §5.8 AST-сторож смотрит в ЖИВОЕ дерево ───────────────────────────
    ("ручной разбор вернулся в chatter/run.py — скан обязан увидеть", RUN,
     [(b"from chatter.core.contact_ref import peer_of",
       b"from chatter.core.contact_ref import peer_of\n\n\n"
       b"def _gate_probe_never_called(contact_id):\n"
       b'    return contact_id.split(":")[0]')],
     GA + "::test_no_raw_contact_id_split_is_left_in_chatter_or_app"),

    # ВСТРЕЧНАЯ ПОЛОВИНА к предыдущей: самая дешёвая правка под сторожа 8 —
    # пройти регуляркой по всем `contact_id.*split` разом, и тогда живой хвост
    # уедет вместе с ними. Односторонний сторож принял бы это за успех.
    ("уборка причесала заодно хвостовой rsplit в _persona_settings", TR,
     [(b'        slug = contact_id.rsplit(":", 1)[-1]',
       b'        slug = contact_id.split(":")[0]')],
     GA + "::test_the_slug_tail_rsplit_survived_the_cleanup"),

    # ── §5 исключение владельца обязано ОСТАВАТЬСЯ заработанным ───────────
    ("владелец разбора перестал определять slug_of — исключение не заработано", CR,
     [(b"def slug_of(contact_id: str) -> str:",
       b"def slug_of_renamed(contact_id: str) -> str:")],
     GA + "::test_the_parse_owner_exemption_is_still_earned"),
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
