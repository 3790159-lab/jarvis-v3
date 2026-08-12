"""DEV-26 на «отказ панели — не тупик» и связку панелей ссылками.

Все мутации здесь тихие: страница отдаётся, статус осмысленный, глазами разницы
нет. Цена каждой — владелец с телефона упирается в ответ, из которого нет выхода,
и это ровно тот дефект, который прожил вечер незамеченным.

Прогон: python scripts/mutate_panels_no_dead_end.py (дерево должно быть чистым).
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
MAIN = "app/main.py"
TD = "app/routers/tamapi_dashboard.py"
JP = "app/routers/jarvis_panel.py"
TW = "tests/chatter/test_panels_web.py"
TG = "tests/test_panel_routes_owner_guarded.py"

MUTATIONS = [
    ("отказ снова голый 401 — из него нет выхода", PA,
     [("        raise PanelLoginRequired()",
       '        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "owner key required")')],
     f"{TW}::test_browser_without_cookie_is_sent_to_the_login_form"),

    ("отказала не одна панель — сторож клиентской", PA,
     [("        raise PanelLoginRequired()",
       '        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "owner key required")')],
     f"{TW}::test_browser_without_cookie_on_the_client_panel_too"),

    ("редирект достаётся и машине — красный приёмки станет зелёным", PA,
     [('    if request.method != "GET":\n        return False\n'
       '    return "text/html" in (request.headers.get("accept") or "")',
       "    return True")],
     f"{TW}::test_a_machine_client_still_gets_401_and_not_a_redirect"),

    ("POST тоже уводится на форму — тело действия теряется молча", PA,
     [('    if request.method != "GET":\n        return False\n', "")],
     f"{TW}::test_a_post_without_cookie_is_refused_not_redirected"),

    ("`next` берётся из адреса как есть, мимо белого списка", PA,
     [("    target = _safe_next(request.url.path)",
       "    target = request.url.path")],
     f"{TW}::test_the_return_target_stays_inside_the_whitelist"),

    ("обработчик отказа не поставлен в main.py", MAIN,
     [("        install_panel_auth_redirect(app)\n", "")],
     f"{TG}::test_main_wires_the_panel_login_redirect"),

    ("ссылка «Ферма» пропала с клиентской панели", TD,
     [("\n <a href='/panel/jarvis'>Ферма →</a>", "")],
     f"{TW}::test_client_panel_links_to_the_farm"),

    ("обратная ссылка пропала с фермы", JP,
     [("\n<div style='margin-top:16px'><a href='/panel/tamapi'>← Клієнти</a></div>", "")],
     f"{TW}::test_farm_links_back_to_the_client_panel"),
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
