"""DEV-26 на указатель `/panel` — короткий адрес панели для телефона.

Ручка ничего не показывает, поэтому её дефекты не видно глазами: она либо ведёт
не туда, либо не пускает вовсе, либо тихо открывает миру соседний субтри. Каждая
мутация здесь — правдоподобная «упрощённая» версия того же кода.

Прогон: python scripts/mutate_panel_root_redirect.py (дерево должно быть чистым).
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
# неотличимы — вторая исполнилась бы байткодом первой (DEV-26).
_MTIME_BASE = 2_000_000_000
_mtime_seq = count()

PA = "app/routers/panels_auth.py"
MW = "app/services/auth_middleware.py"
TW = "tests/chatter/test_panels_web.py"
TE = "tests/test_auth_enforce.py"

# Блок ЦЕЛИКОМ, потому что строка `provided = ...` встречается и в
# `require_owner` выше — а `replace(..., 1)` взял бы именно её.
_ROOT_BODY = """    _require_enabled()
    provided = x_panels_key or request.cookies.get(_COOKIE) or ""
    known = bool(provided) and _same(provided, _expected())"""

MUTATIONS = [
    ("указатель верит НАЛИЧИЮ cookie, а не её годности", PA,
     [("known = bool(provided) and _same(provided, _expected())",
       "known = bool(provided)")],
     f"{TW}::test_panel_root_with_a_stale_cookie_goes_to_the_form_not_a_dead_end"),

    ("указатель всегда шлёт на форму — владелец ходит через лишний экран", PA,
     [('return RedirectResponse(_NEXT_DEFAULT if known else "/panel/login",',
       'return RedirectResponse("/panel/login",')],
     f"{TW}::test_panel_root_sends_the_owner_to_the_dashboard"),

    ("указатель всегда шлёт на панель — чужой упирается в 401 вместо формы", PA,
     [('return RedirectResponse(_NEXT_DEFAULT if known else "/panel/login",',
       'return RedirectResponse(_NEXT_DEFAULT,')],
     f"{TW}::test_panel_root_sends_a_stranger_to_the_login_form"),

    ("указатель перестал читать ключ из заголовка", PA,
     [(_ROOT_BODY, _ROOT_BODY.replace(
         'provided = x_panels_key or request.cookies.get(_COOKIE) or ""',
         'provided = request.cookies.get(_COOKIE) or ""'))],
     f"{TW}::test_panel_root_accepts_the_header_too"),

    # Без этой строки enforce-middleware глушит /panel ЕЩЁ ДО роутера, и симптом
    # неотличим от «неверный ключ» — ровно та стена, что съела вечер 12.08.
    ("/panel убран из PUBLIC_EXACT — middleware глушит его до роутера", MW,
     [('        "/panel",\n', "")],
     f"{TE}::test_is_public_path_final"),

    # «Упрощение», которое выглядит аккуратнее и открывает миру лишнее.
    ("/panel открыт префиксом вместо точного совпадения — с ним и /panelling/*", MW,
     [('        "/panel",\n', ""),
      ('PUBLIC_PREFIXES = ("/api/jarvis/ops/", "/panel/")',
       'PUBLIC_PREFIXES = ("/api/jarvis/ops/", "/panel")')],
     f"{TE}::test_is_public_path_final"),
]


def write_mutant(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    stamp = _MTIME_BASE + next(_mtime_seq)
    os.utime(path, (stamp, stamp))


def run(test: str) -> bool:
    """True = тест зелёный."""
    # encoding задан явно: под Windows `text=True` берёт cp1251 и роняет
    # читающий поток на первом кириллическом ассерте — прогон «проходит», а
    # вывод теряется ровно там, где мутация что-то нашла.
    p = subprocess.run(
        [sys.executable, "-m", "pytest", test, "-q", "--no-header",
         "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    return p.returncode == 0


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
            green = run(test)
        finally:
            revert(rel)
        if green:
            print(f"[СЛЕП] {name}\n        {test} остался ЗЕЛЁНЫМ")
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
