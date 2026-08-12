"""DEV-26 для двери панели (форма вместо ссылки с ключом): ломаем каждую
правку обратно и смотрим, кто покраснеет.

Дверь — единственное место, где ключ владельца принимается снаружи, поэтому
слепой сторож здесь стоит дороже прочих: он не «пропустит регрессию», он
оставит открытым вход в переписку живых лидов.

Отличие от `mutate_panels_stopall.py`: мутация может состоять из НЕСКОЛЬКИХ
замен. Восстановить старую дверь одной строкой нельзя — у ручки меняется и
сигнатура, и тело, а мутант-полуфабрикат ничего не доказывает.

Прогон: python scripts/mutate_panels_login_form.py (дерево должно быть чистым).
"""
from __future__ import annotations

import os
import subprocess
import sys
from itertools import count
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]   # работает и в worktree

# Python признаёт кэш байткода актуальным по паре (mtime в ЦЕЛЫХ секундах,
# размер). Две мутации одного файла с одинаковым размером в одну секунду
# неотличимы — вторая исполнится байткодом первой и отчитается как [ok],
# ничего не проверив. Лечим уникальным mtime на каждую запись (DEV-26).
_MTIME_BASE = 2_000_000_000
_mtime_seq = count()

A = "app/routers/panels_auth.py"
T = "tests/chatter/test_panels_web.py"

# (имя, файл, [(что заменить, на что), ...], какой тест ОБЯЗАН покраснеть)
MUTATIONS = [
    # ── суть замены: ключ из URL больше не открывает дверь ───────────────
    ("ключ из query снова пускает внутрь (старая ссылка ожила)", A,
     [("async def login_page(next: str = _NEXT_DEFAULT) -> HTMLResponse:",
       "async def login_page(request: Request, key: str = \"\","
       " next: str = _NEXT_DEFAULT):"),
      ("    _require_enabled()\n    return HTMLResponse(_form_html(_safe_next(next)))",
       "    _require_enabled()\n"
       "    if key and _same(key, _expected()):\n"
       "        r = RedirectResponse(_safe_next(next), status_code=303)\n"
       "        r.set_cookie(_COOKIE, _expected(), httponly=True,"
       " samesite='strict', path='/panel')\n"
       "        return r\n"
       "    return HTMLResponse(_form_html(_safe_next(next)))")],
     f"{T}::test_a_valid_key_in_the_url_no_longer_logs_anybody_in"),

    # ── выключенная панель не имеет права держать открытой ПОЛОВИНУ двери ─
    ("страница формы жива при выключенной панели", A,
     [("    _require_enabled()\n    return HTMLResponse(_form_html(_safe_next(next)))",
       "    return HTMLResponse(_form_html(_safe_next(next)))")],
     f"{T}::test_the_login_route_itself_is_dead_while_panels_are_disabled"),

    ("приём формы жив при выключенной панели", A,
     [("    _require_enabled()\n    form = await request.form()",
       "    form = await request.form()")],
     f"{T}::test_the_login_route_itself_is_dead_while_panels_are_disabled"),

    # ── куда уводит дверь ────────────────────────────────────────────────
    ("открытый редирект: `next` берётся как прислали", A,
     [("    return raw if raw in _NEXT_ALLOWED else _NEXT_DEFAULT",
       "    return raw or _NEXT_DEFAULT")],
     f"{T}::test_the_redirect_target_cannot_be_an_arbitrary_site"),

    ("в форму подставляется чужой `next`", A,
     [("    return HTMLResponse(_form_html(_safe_next(next)))",
       "    return HTMLResponse(_form_html(next))")],
     f"{T}::test_the_form_carries_a_whitelisted_next_and_drops_the_rest"),

    ("скрытое поле `next` пропало — выбор панели не доезжает до POST", A,
     [("<input type='hidden' name='next' value='{next}'>\n", "")],
     f"{T}::test_the_form_carries_a_whitelisted_next_and_drops_the_rest"),

    ("успех отвечает 302 вместо 303", A,
     [("    resp = RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)",
       "    resp = RedirectResponse(target, status_code=302)")],
     f"{T}::test_posting_the_right_key_sets_the_cookie"),

    # ── свойства cookie ──────────────────────────────────────────────────
    ("cookie достаётся скрипту на странице (HttpOnly снят)", A,
     [("        httponly=True,          # ключ не должен доставаться скрипту на странице",
       "        httponly=False,")],
     f"{T}::test_the_cookie_is_httponly_samesite_strict_and_panel_scoped"),

    ("cookie уезжает по ссылке с чужого сайта (SameSite ослаблен)", A,
     [('        samesite="strict",      # чужой сайт не дёрнет панель от твоего имени',
       '        samesite="lax",')],
     f"{T}::test_the_cookie_is_httponly_samesite_strict_and_panel_scoped"),

    ("cookie с ключом едет на ВСЕ ручки бэкенда (Path=/)", A,
     [('        path="/panel",          # ключ не поедет на остальные ручки бэкенда',
       '        path="/",')],
     f"{T}::test_the_cookie_is_httponly_samesite_strict_and_panel_scoped"),

    # ── сама проверка ключа ──────────────────────────────────────────────
    ("внутрь пускают с любым ключом", A,
     [("    if not key.strip() or not _same(key, _expected()):",
       "    if not key.strip() or False:")],
     f"{T}::test_a_wrong_key_is_refused_and_sets_nothing"),

    ("пустая форма пускает внутрь", A,
     [("    if not key.strip() or not _same(key, _expected()):",
       "    if key.strip() and not _same(key, _expected()):")],
     f"{T}::test_no_key_at_all_is_refused_and_sets_nothing"),

    ("отказ отвечает 200 — «не подошло» неотличимо от «подошло»", A,
     [("                            status_code=status.HTTP_401_UNAUTHORIZED)",
       "                            status_code=200)")],
     f"{T}::test_a_wrong_key_is_refused_and_sets_nothing"),

    ("отказ возвращает введённый ключ в разметку", A,
     [("        return HTMLResponse(_form_html(target, error=True),",
       "        return HTMLResponse(_form_html(target, error=True) + key,")],
     f"{T}::test_the_key_is_not_echoed_back_in_the_body"),

    # ── сама форма ───────────────────────────────────────────────────────
    ("поле ключа стало обычным текстом — ключ виден на экране", A,
     [("<input type='password' name='key' autocomplete='current-password'",
       "<input type='text' name='key' autocomplete='current-password'")],
     f"{T}::test_login_page_is_a_password_form"),

    ("форма шлёт ключ методом GET — то есть снова в URL", A,
     [("<form method='post' action='/panel/login' autocomplete='on'>",
       "<form method='get' action='/panel/login' autocomplete='on'>")],
     f"{T}::test_login_page_is_a_password_form"),
]


def write_mutant(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    stamp = _MTIME_BASE + next(_mtime_seq)
    os.utime(path, (stamp, stamp))


def run(test: str) -> bool:
    """True = тест зелёный."""
    p = subprocess.run(
        [sys.executable, "-m", "pytest", test, "-q", "--no-header",
         "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True)
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
