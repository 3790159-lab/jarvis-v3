# -*- coding: utf-8 -*-
"""Мутационный гейт на сторожей «имя клиента на панели» (спека 2026-08-20).

Зачем отдельный гейт именно здесь. Два сторожа из семи — `..._sounds_exactly_once`
и `..._stands_in_the_header_next_to_the_slug` — были ЗЕЛЁНЫМИ на старом экране,
где имя «Ольга» стояло литералом. Это ровно тот случай, ради которого гейт и
существует: сторож, который проходит и на дефекте, доказательством не является,
и отличить его от настоящего можно только сломав код нарочно.

Мутации бьют по РЕШЕНИЯМ спеки, а не по словам разметки:
источник имени (конфиг, не env), отсутствие тихого дефолта при нечитаемом
конфиге, отсутствие зашитого имени в шапке и отсутствие зашитого РОДА персоны.

Прогон: python scripts/mutate_panel_client_name.py (дерево должно быть чистым).
"""
from __future__ import annotations

import os
import subprocess
import sys
from itertools import count
from pathlib import Path

from gate_guard import refuse_if_live_tree   # DEV-31: гейт мутирует только worktree

ROOT = Path(__file__).resolve().parents[1]   # работает и в worktree

# Уникальный mtime на каждую запись: Python признаёт кэш байткода актуальным по
# паре (mtime в целых секундах, размер), и две мутации одного размера в одну
# секунду неотличимы — вторая исполнится байткодом первой (DEV-26).
_MTIME_BASE = 2_000_000_000
_mtime_seq = count()

TD = "app/routers/tamapi_dashboard.py"
T = "tests/test_panel_client_name.py"

# (имя, файл, что заменить, на что, какой тест ОБЯЗАН покраснеть)
MUTATIONS = [
    ("шапка снова зовёт клиента зашитым именем", TD,
     "<div class='sub'>{esc(_persona_name())} · TAMAPI",
     "<div class='sub'>Ольга · TAMAPI",
     f"{T}::test_two_clients_see_their_own_name_and_not_the_other"),

    ("имя берётся из переменной окружения, а не из конфига", TD,
     '    cfg = _cfg()\n'
     '    name = getattr(getattr(cfg, "settings", None), "persona_name", None)',
     '    name = os.getenv("TAMAPI_PERSONA_NAME")',
     f"{T}::test_the_name_comes_from_persona_name_not_from_env"),

    ("нечитаемый конфиг подставляет тихий дефолт вместо отказа", TD,
     '    return (name or "").strip() or NAME_UNREADABLE',
     '    return (name or "").strip() or "TAMAPI"',
     f"{T}::test_unreadable_config_refuses_loudly_and_does_not_kill_the_screen"),

    ("копирайт паузы снова согласован по роду с женской персоной", TD,
     "  <p>Бот перестане відповідати <b>ВСІМ</b> лідам, доки ви не увімкнете його назад.",
     "  <p>Вона перестане відповідати <b>ВСІМ</b> лідам, доки ви не увімкнете її назад.",
     f"{T}::test_no_persona_gender_is_hardcoded_in_the_pause_copy"),

    ("подтверждение паузы снова называет клиента по имени", TD,
     '            "feedback": "Зупинити бота ВСІМ лідам? Підтвердіть ще раз.",',
     '            "feedback": "Зупинити Ольгу ВСІМ лідам? Підтвердіть ще раз.",',
     f"{T}::test_no_client_name_is_hardcoded_in_the_screen"),
]


def write_mutant(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    stamp = _MTIME_BASE + next(_mtime_seq)
    os.utime(path, (stamp, stamp))


def run(test: str) -> tuple[bool, str]:
    """(поймана ли мутация, чем именно ответил pytest).

    КРАСНОЕ — это РОВНО `rc 1` плюс `failed` в выводе, а не «любой ненулевой
    rc» (техдолг DEV-26): мутация, сломавшая СБОР тестов, отвечает rc 2/4/5 и
    прежним критерием печаталась бы как пойманная, не выполнив ни одной
    проверки. encoding задан явно — cp1251 не знает байта из «И», и одна
    заглавная буква в чужом ассерте меняла бы вердикт гейта.
    """
    p = subprocess.run(
        [sys.executable, "-m", "pytest", test, "-q", "--no-header",
         "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    out = (p.stdout or "") + (p.stderr or "")
    tail = out.strip().splitlines()[-1] if out.strip() else "(пусто)"
    return (p.returncode == 1 and "failed" in out,
            "rc=%d | %s" % (p.returncode, tail[:140]))


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
    refuse_if_live_tree(ROOT)
    assert_clean()
    blind = []
    for name, rel, old, new, test in MUTATIONS:
        path = ROOT / rel
        text = path.read_text(encoding="utf-8")
        if old not in text:
            # Не применившаяся мутация — это НЕ «ok»: она ничего не проверила.
            print(f"[!] МУТАЦИЯ НЕ ПРИМЕНИЛАСЬ: {name} — фрагмент не найден")
            blind.append((name, "фрагмент не найден"))
            continue
        write_mutant(path, text.replace(old, new, 1))
        try:
            caught, why = run(test)
        finally:
            revert(rel)
        if not caught:
            print(f"[СЛЕП] {name}\n        {test}\n        {why}")
            blind.append((name, test))
        else:
            print(f"[ok]   {name} -> сторож покраснел")
    print()
    if blind:
        print(f"СЛЕПЫХ СТОРОЖЕЙ: {len(blind)} из {len(MUTATIONS)}")
        return 1
    print(f"Все {len(MUTATIONS)} мутаций пойманы.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
