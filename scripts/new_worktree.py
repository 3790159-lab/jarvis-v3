"""DEV-31: создать worktree, в котором можно работать и гонять гейты.

Почему это скрипт, а не три команды в голове. Голый `git worktree add` даёт
дерево, в котором:

* нет gitignored-состава — `requisites.yaml` и соседи не трекаются, и baseline
  снимается ДРУГИМ набором файлов: 13.08 это дало 22 ложных падения, которые
  читались как регресс кода;
* один legacy-файл сразу числится модифицированным — `jarvis_claude_review_pack/
  app__main.py` лежит на диске ПОБАЙТОВО равным блобу (проверено:
  `hash-object --no-filters` == `rev-parse :путь`), но `.gitattributes` требует
  `eol=lf`, и git считает его правкой. Мутационный гейт отказывается работать
  на грязном дереве — то есть свежий worktree сразу непригоден для того,
  ради чего создан.

Оба препятствия — среда, а не код, и оба чинятся один раз здесь, а не заново
каждой сессией по памяти.

Прогон:
    .venv\\Scripts\\python.exe scripts/new_worktree.py --name panel-fix \\
        --branch fix/journal-colspan
    .venv\\Scripts\\python.exe scripts/new_worktree.py --name arc-x \\
        --new-branch arc/x            # ветку создаст от транка
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSITION = ROOT / "scripts" / "worktree_composition.txt"
WORKTREE_HOME = Path("C:/jarvis_worktrees")
TRUNK = "phase-4.0-unified-jarvis"


LINK_MARK = "link "


def composition_entries(text: str) -> list[tuple[str, str]]:
    """[(способ, glob), ...]. Способ — `link` или `copy`.

    `link` — для файлов с живыми ключами: жёсткая ссылка держит данные ОДНОЙ
    записью на диске, поэтому второй копии секрета не появляется, ротация
    доезжает во все worktree сама, и протухшая копия ключа не притворяется
    живой (решение владельца 15.08).
    """
    out = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith(LINK_MARK):
            out.append(("link", line[len(LINK_MARK):].strip()))
        else:
            out.append(("copy", line))
    return out


def composition_globs(text: str) -> list[str]:
    """Только пути, без способа доставки."""
    return [pattern for _mode, pattern in composition_entries(text)]


def git(*args, cwd, check=True) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise SystemExit("git %s → rc=%d\n%s%s"
                         % (" ".join(args), proc.returncode, proc.stdout, proc.stderr))
    return proc.stdout


def modified_paths(porcelain: str) -> list[str]:
    """Пути из `git status --porcelain`, как их видит git.

    Статус-буквы снимаются: « M x» и «M  x» — одно и то же состояние файла.
    """
    out = []
    for line in porcelain.splitlines():
        if len(line) > 3:
            out.append(line[3:].strip().strip('"'))
    return out


def is_normalization_artifact(rel: str, tree: Path) -> bool:
    """True = файл на диске ПОБАЙТОВО равен блобу, и «правка» это перевод строк.

    Сравниваются хэши: `--no-filters` берёт байты с диска как есть, без
    приведения EOL, а `rev-parse :путь` даёт то, что лежит в индексе. Равенство
    доказывает, что содержимое не трогали, а различие означает НАСТОЯЩУЮ
    правку — в свежем worktree её быть не может, и молча гасить её нельзя.
    """
    blob = git("rev-parse", ":" + rel, cwd=tree, check=False).strip()
    disk = git("hash-object", "--no-filters", rel, cwd=tree, check=False).strip()
    return bool(blob) and blob == disk


def settle(tree: Path) -> list[str]:
    """Погасить артефакты нормализации. Возвращает список погашенных путей."""
    dirty = modified_paths(git("status", "--porcelain", "--untracked-files=no", cwd=tree))
    hushed = []
    for rel in dirty:
        if not is_normalization_artifact(rel, tree):
            raise SystemExit(
                "ОТКАЗ: в СВЕЖЕМ worktree файл отличается от индекса по "
                "содержимому: %s\nЭто не перевод строк. Разберись, прежде чем "
                "снимать в этом дереве baseline." % rel)
        git("update-index", "--skip-worktree", rel, cwd=tree)
        hushed.append(rel)
    return hushed


def deliver(found: Path, target: Path, mode: str) -> str:
    """Положить файл в worktree. Возвращает способ, каким это вышло.

    Провал `os.link` не молчит и не остаётся провалом: секрет обязан доехать,
    иначе baseline снова снимается другим составом. Но подмена ссылки копией
    ДОЛЖНА быть названа вслух — у копии другое свойство (она протухает), и
    молчаливая подмена превратила бы это в невидимую разницу между worktree.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    if mode == "link":
        try:
            os.link(found, target)
            return "link"
        except OSError as exc:
            shutil.copy2(found, target)
            return "copy-вместо-link (%s)" % exc.__class__.__name__
    shutil.copy2(found, target)
    return "copy"


def copy_composition(src: Path, dest: Path, entries) -> list[str]:
    """Разложить gitignored-состав. Возвращает строки «путь (способ)».

    Принимает и список пар из `composition_entries`, и голый список globов —
    во втором случае всё копируется.
    """
    laid = []
    for entry in entries:
        mode, pattern = entry if isinstance(entry, tuple) else ("copy", entry)
        for found in sorted(src.glob(pattern)):
            if not found.is_file():
                continue
            rel = found.relative_to(src)
            how = deliver(found, dest / rel, mode)
            laid.append("%s (%s)" % (str(rel).replace("\\", "/"), how))
    return laid


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="worktree с полным составом (DEV-31)")
    ap.add_argument("--name", required=True, help="имя каталога в C:/jarvis_worktrees")
    ap.add_argument("--branch", help="существующая ветка")
    ap.add_argument("--new-branch", help="создать ветку от --from")
    ap.add_argument("--from", dest="base", default=TRUNK, help="база для --new-branch")
    ap.add_argument("--home", default=str(WORKTREE_HOME))
    args = ap.parse_args(argv)

    if bool(args.branch) == bool(args.new_branch):
        raise SystemExit("Нужно ровно одно: --branch ИЛИ --new-branch.")

    dest = Path(args.home) / args.name
    if dest.exists():
        raise SystemExit("ОТКАЗ: %s уже существует." % dest)

    if args.new_branch:
        git("worktree", "add", "-b", args.new_branch, str(dest), args.base, cwd=ROOT)
    else:
        git("worktree", "add", str(dest), args.branch, cwd=ROOT)
    print("worktree: %s" % dest)

    entries = composition_entries(COMPOSITION.read_text(encoding="utf-8"))
    laid = copy_composition(ROOT, dest, entries)
    # Состав называется поимённо, а не числом: «доставлено 3» не отличает
    # «приехал нужный файл» от «приехал не тот».
    print("состав (%d):" % len(laid))
    for line in laid:
        print("  + %s" % line)
    for _mode, pattern in entries:
        if not list(ROOT.glob(pattern)):
            print("  ⚠️ ничего не нашлось по: %s" % pattern)

    hushed = settle(dest)
    if hushed:
        print("артефакты нормализации погашены (--skip-worktree), "
              "содержимое побайтово равно индексу:")
        for rel in hushed:
            print("  ~ %s" % rel)

    left = git("status", "--porcelain", "--untracked-files=no", cwd=dest).strip()
    if left:
        raise SystemExit("ОТКАЗ: дерево осталось грязным:\n" + left)
    print("дерево чистое — гейт здесь запустится.")
    print("\nдальше:")
    print("  cd %s" % dest)
    print("  C:/jarvis/.venv/Scripts/python.exe -m pytest tests/ -q")
    return 0


if __name__ == "__main__":
    sys.exit(main())
