"""DEV-26 на роль владельца из конфига клиента (DEV-29, схема C).

Предикат `mentions_owner_contact` стоит на ДВУХ швах с разной ценой ошибки:
лишняя карточка владельцу (`owner_handoff`) — и ЗАМЕНА готового ответа заглушкой
(гейт H2 в run.py при недоставленной карточке). Поэтому мутации здесь целятся не
в «работает/не работает», а в тихие УПРОЩЕНИЯ, каждое из которых выглядит
аккуратнее оригинала и молча возвращает ложные срабатывания:

  M1 — конфиг выключен, роль держит только хардкод (дыра третьего клиента);
  M2 — бюджет хвоста из конфига бесконечен (схема C деградирует до B);
  M3 — стем из ВСЕХ пар токенов (схема C деградирует до A: 6 ложных из 7);
  M4 — матч без бюджета вообще (возврат к подстроке-префиксу);
  M5 — грубый стем ИМЕНИ работает и при наличии пары форм (§7: «керівництво»).

Прогон: python scripts/mutate_owner_role_stems.py (дерево должно быть чистым).
"""
from __future__ import annotations

import os
import subprocess
import sys
from itertools import count
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]   # работает и в worktree

# Уникальный mtime на каждую запись: кэш байткода признаётся актуальным по паре
# (mtime в целых секундах, размер), и две мутации одного размера в одну секунду
# неотличимы — вторая исполнилась бы байткодом первой (jarvis-dev26-gate-stale-pyc).
_MTIME_BASE = 2_000_000_000
_mtime_seq = count()

ESC = "chatter/core/escalation.py"
T = "tests/chatter/test_escalation.py"

_SPEC_TAIL = """    a, b = a_words[-1], b_words[-1]
    stem = _common_prefix(a, b)
    if len(stem) < _MIN_ROLE_STEM:
        return None
    return stem, max(len(a) - len(stem), len(b) - len(stem))"""

_SCHEME_A = """    best = None
    for a in a_words:
        for b in b_words:
            stem = _common_prefix(a, b)
            if len(stem) >= _MIN_ROLE_STEM and (best is None or len(stem) > len(best[0])):
                best = (stem, max(len(a) - len(stem), len(b) - len(stem)))
    return best"""

MUTATIONS = [
    ("M1 конфиг выключен: стем роли не выводится вовсе", ESC,
     [("    a_words, b_words = _words(owner_id), _words(owner_ref)",
       "    return None\n    a_words, b_words = _words(owner_id), _words(owner_ref)")],
     f"{T}::test_owner_role_from_config_catches_declined_forms"),

    ("M2 бюджет хвоста из конфига бесконечен (схема C -> B)", ESC,
     [("    return stem, max(len(a) - len(stem), len(b) - len(stem))",
       "    return stem, 99")],
     f"{T}::test_owner_role_stem_budget_bounds_the_tail"),

    ("M2b тот же мутант ловится и негативным контролем ниши", ESC,
     [("    return stem, max(len(a) - len(stem), len(b) - len(stem))",
       "    return stem, 99")],
     f"{T}::test_owner_role_from_config_negative_control"),

    ("M3 стем из ВСЕХ пар токенов (схема C -> A, 6 ложных из 7)", ESC,
     [(_SPEC_TAIL, _SCHEME_A)],
     f"{T}::test_owner_role_from_config_negative_control"),

    ("M4 матч игнорирует бюджет — возврат к подстроке-префиксу", ESC,
     [("    return any(w.startswith(stem) and len(w) - len(stem) <= budget for w in words)",
       "    return any(w.startswith(stem) for w in words)")],
     f"{T}::test_owner_role_stem_budget_bounds_the_tail"),

    ("M4b тот же мутант ловится сторожем хардкодных стемов (§7)", ESC,
     [("    return any(w.startswith(stem) and len(w) - len(stem) <= budget for w in words)",
       "    return any(w.startswith(stem) for w in words)")],
     f"{T}::test_hardcoded_role_stems_reject_longer_words"),

    ("M5 грубый стем ИМЕНИ работает и при наличии пары форм (§7.1)", ESC,
     [("    if spec is None:\n        if oid and len(oid) >= 6 and oid[:-1] in low:",
       "    if True:\n        if oid and len(oid) >= 6 and oid[:-1] in low:")],
     f"{T}::test_owner_name_crude_stem_only_without_a_form_pair"),
]

# Сторожа, которые обязаны остаться ЗЕЛЁНЫМИ на каждом мутанте: запасной набор и
# demo/demo2 не должны зависеть ни от конфига, ни от бюджета. Мутация, которая
# роняет ВСЁ подряд, ничего не доказывает — она просто ломает импорт.
ALWAYS_GREEN = [
    f"{T}::test_owner_role_hardcode_alive_without_config",
    f"{T}::test_owner_role_empty_owner_ref_behaves_as_today",
]


def write_mutant(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    stamp = _MTIME_BASE + next(_mtime_seq)
    os.utime(path, (stamp, stamp))


def run(test: str) -> int:
    """Код возврата pytest. encoding задан явно: под Windows text=True берёт
    cp1251 и роняет читающий поток на первом кириллическом ассерте."""
    p = subprocess.run(
        [sys.executable, "-m", "pytest", test, "-q", "--no-header",
         "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    return p.returncode


def revert(rel: str) -> None:
    subprocess.run(["git", "checkout", "--", rel], cwd=ROOT, check=True)


def assert_clean() -> None:
    """Откат идёт через `git checkout --`, то есть НЕЗАКОММИЧЕННОЕ он сотрёт."""
    out = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                         cwd=ROOT, capture_output=True, text=True).stdout.strip()
    if out:
        raise SystemExit(
            "ОТКАЗ: рабочее дерево грязное — откат мутаций сотрёт эти правки.\n"
            "Закоммить их и повтори прогон:\n" + out)


def main() -> int:
    assert_clean()
    blind = []
    for name, rel, edits, test in MUTATIONS:
        path = ROOT / rel
        text = path.read_text(encoding="utf-8")
        mutated = text
        missing = [old for old, _new in edits if old not in mutated]
        if missing:
            # Не применившаяся мутация — это НЕ «ok»: она ничего не проверила.
            print(f"[!] МУТАЦИЯ НЕ ПРИМЕНИЛАСЬ: {name} — фрагмент не найден")
            blind.append((name, "фрагмент не найден"))
            continue
        for old, new in edits:
            mutated = mutated.replace(old, new, 1)
        write_mutant(path, mutated)
        try:
            rc = run(test)
            green_rcs = [(g, run(g)) for g in ALWAYS_GREEN]
        finally:
            revert(rel)
        if rc == 0:
            print(f"[СЛЕП] {name}\n        {test} остался ЗЕЛЁНЫМ")
            blind.append((name, test))
            continue
        if rc != 1:
            # Класс «гейт врёт»: rc 4 = узел не найден (тест переименовали),
            # rc 2/3/5 = ошибка сбора/импорта. «Не 0» тут НЕ значит «покраснел».
            print(f"[ВРЁТ] {name}\n        {test} дал rc={rc}, а не 1 — "
                  "это не красный тест, а сломанный прогон")
            blind.append((name, f"rc={rc}"))
            continue
        broken = [g for g, grc in green_rcs if grc != 0]
        if broken:
            print(f"[ШИРОКО] {name}\n        мутант уронил и то, что не должен: "
                  + ", ".join(broken))
            blind.append((name, "уронил ALWAYS_GREEN"))
            continue
        print(f"[ok]   {name} -> сторож покраснел (rc 1), запасной набор цел")
    print()
    if blind:
        print(f"СЛЕПЫХ/ВРУЩИХ: {len(blind)} из {len(MUTATIONS)}")
        for name, why in blind:
            print(f"  - {name}: {why}")
        return 1
    print(f"Все {len(MUTATIONS)} мутаций пойманы, красный = ровно rc 1.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
