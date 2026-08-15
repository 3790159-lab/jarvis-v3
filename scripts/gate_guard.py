"""DEV-31: мутационный гейт не мутирует дерево, из которого поднимается прод.

Гейт пишет в файл заведомо сломанную версию и держит её ровно на время одного
прогона pytest — секунды. Триггер у гардиана ровно один: смерть процесса; ни
про git, ни про гейт он не знает ничего. Совпади смерть с этими секундами — в
прод уедет МУТАНТ, и следа не останется: гейт откатит файл сразу после, а
поднятый процесс доживёт со сломанным кодом до следующей смерти.

Это не гипотеза. Журнал панели за 15.08 сам записал прод-дерево с мутантом
внутри (`dirty: app/routers/jarvis_panel.py`, `dirty: app/services/jarvis_farm.py`,
`dirty: scripts/ops_watchdog.py`) — проба `worktree` ходит раз в 30 секунд и
успевала застать окно мутации. Не поймала только потому, что грязное дерево
во время работы над аркой читалось как норма.

Отсюда правило: гейты запускаются ТОЛЬКО в worktree.

Отказ идёт по двум независимым сигналам, любого хватает:

1. **Факт** — процессы, ЗАПУЩЕННЫЕ из этого дерева. Именно запущенные, а не
   упомянувшие его: панель уже разбилась ровно об это различие (14.08,
   исключение по родству), и свой собственный процесс в список попадать не
   имеет права — иначе гейт отказывал бы себе, а не гардиану.
2. **Устройство** — это ГЛАВНОЕ дерево репозитория. Абсолютные пути в задачах
   гардианов прибиты именно к нему, поэтому оно остаётся источником деплоя,
   даже когда ферма целиком лежит и сигнал 1 молчит.

Сигнал 1 нужен затем, чтобы правило не сломалось, если завтра живым деревом
сделают worktree: устройство тогда соврёт, живые процессы — нет. Сигнал 2 —
затем, что «ферма не запущена» это не «дерево безопасно»: задача гардиана
поднимет её в любую секунду.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SCRIPT_SUFFIXES = (".py", ".ps1")


def _norm(value) -> str:
    """Абсолютный путь в сравнимом виде (Windows не различает регистр)."""
    text = str(value or "").strip().strip('"')
    if not text:
        return ""
    try:
        return os.path.normcase(os.path.abspath(text))
    except (OSError, ValueError):
        return ""


def under(path, root) -> bool:
    """True = `path` лежит ВНУТРИ `root` (или совпадает с ним).

    Сравнение по разделителю, а не по префиксу строки: без него
    `C:/jarvis_worktrees/...` считался бы лежащим внутри `C:/jarvis`, и гейт
    отказывал бы работать ровно там, куда мы его и загоняем.

    ОТНОСИТЕЛЬНЫЙ путь не привязывается к дереву вовсе. Псевдопроцессы Windows
    (`Registry`, `MemCompression`) отдают вместо пути голое имя, а `abspath`
    доклеивает к нему ТЕКУЩИЙ каталог — то есть каталог гейта. Гейт из-за этого
    отказал сам себе в собственном worktree (замерено 15.08). Чужой рабочий
    каталог нам неизвестен, и гадать о нём нельзя.
    """
    raw = str(path or "").strip().strip('"')
    if not raw or not os.path.isabs(raw):
        return False
    p, r = _norm(raw), _norm(root)
    if not p or not r:
        return False
    return p == r or p.startswith(r.rstrip("\\/") + os.sep)


def launched_script(cmdline) -> str | None:
    """Путь скрипта, который процесс ИСПОЛНЯЕТ, либо None.

    Запуск ≠ упоминание: `python -c "... C:/jarvis/app ..."` называет дерево в
    своей командной строке, но не исполняет из него ни строки, и считать его
    деплой-процессом значит запретить гейт по чужому тексту.
    """
    toks = list(cmdline or [])[1:]
    for i, tok in enumerate(toks):
        if str(tok).lower() == "-file" and i + 1 < len(toks):
            return str(toks[i + 1]).strip('"')
    for tok in toks:
        tok = str(tok)
        if tok.startswith("-"):
            continue
        # Первый не-флаг — либо скрипт, либо значение флага (`-m модуль`,
        # `-c код`). Во втором случае молчим: гадать, что это было, значит
        # заводить ложные срабатывания на пустом месте.
        return tok.strip('"') if tok.lower().endswith(SCRIPT_SUFFIXES) else None
    return None


def live_table():
    """(pid, exe, cmdline) по живым процессам.

    Нет psutil — это ОТКАЗ, а не «проверить нечем, поехали»: гейт, который при
    сломанной проверке продолжает работать, охраняет ровно ничего.
    """
    try:
        import psutil
    except ImportError as exc:                                    # pragma: no cover
        raise SystemExit(
            "ОТКАЗ: psutil недоступен (%s) — проверить, живое ли дерево, нечем.\n"
            "Гейт мутирует файлы, из которых гардиан поднимает прод, поэтому "
            "без проверки он не запускается." % exc)
    rows = []
    for proc in psutil.process_iter(["pid", "exe", "cmdline"]):
        info = proc.info or {}
        rows.append((info.get("pid"), info.get("exe") or "", info.get("cmdline") or []))
    return rows


def self_and_parents() -> set:
    """PID гейта и всех его родителей.

    Свой процесс исключается ИМЕНЕМ этого шага, а не случайно: гейт запускается
    интерпретатором из `C:/jarvis/.venv`, то есть в живом дереве совпал бы сам
    с собой и объявил бы деплой-процессом себя. Отказ был бы верным по итогу и
    неверным по причине — а чинят по причине.
    """
    pids = {os.getpid()}
    try:
        import psutil
        for parent in psutil.Process().parents():
            pids.add(parent.pid)
    except Exception:                                             # pragma: no cover
        pass
    return pids


def deployers(root, table=None, *, exclude=()) -> list:
    """[(pid, почему), ...] — процессы, поднятые ИЗ `root`."""
    rows = live_table() if table is None else list(table)
    skip = {pid for pid in exclude}
    found = []
    for pid, exe, cmdline in rows:
        if pid in skip:
            continue
        if under(exe, root):
            found.append((pid, "интерпретатор из дерева: %s" % exe))
            continue
        script = launched_script(cmdline)
        if script and under(script, root):
            found.append((pid, "исполняет из дерева: %s" % script))
    return found


def is_main_worktree(root) -> bool:
    """True = это главное дерево репозитория, а не linked worktree.

    Ошибку git считаем «главным деревом»: не сумев спросить, гейт обязан
    отказать, а не разрешить.
    """
    def ask(flag: str) -> str:
        proc = subprocess.run(["git", "rev-parse", flag], cwd=str(root),
                              capture_output=True, text=True)
        if proc.returncode != 0:
            return ""
        return _norm(Path(str(root)) / proc.stdout.strip())

    git_dir, common = ask("--git-dir"), ask("--git-common-dir")
    if not git_dir or not common:
        return True
    return git_dir == common


def refusal_reasons(root, *, table=None, main_worktree=None) -> list:
    """Причины, по которым в этом дереве мутировать нельзя. Пусто = можно."""
    reasons = []
    is_main = is_main_worktree(root) if main_worktree is None else main_worktree
    if is_main:
        reasons.append("это ГЛАВНОЕ дерево репозитория — задачи гардианов "
                       "прибиты к нему абсолютными путями и поднимут прод "
                       "отсюда даже при лежащей ферме")
    for pid, why in deployers(root, table, exclude=self_and_parents()):
        reasons.append("живой процесс PID %s — %s" % (pid, why))
    return reasons


def refuse_if_live_tree(root, *, gate=None, table=None, main_worktree=None) -> None:
    """Отказать, если `root` — дерево, из которого поднимается прод."""
    reasons = refusal_reasons(root, table=table, main_worktree=main_worktree)
    if not reasons:
        return
    name = gate or Path(sys.argv[0]).name or "гейт"
    raise SystemExit(
        "ОТКАЗ: %s мутирует файлы в %s, а из этого дерева поднимается прод.\n"
        "%s\n\n"
        "Мутант живёт в файле секунды; смерть процесса в это окно = молчаливый "
        "выкат сломанного кода (DEV-31).\n"
        "Сделай worktree и прогоняй гейт там:\n"
        "  .venv\\Scripts\\python.exe scripts/new_worktree.py --name <имя> "
        "--branch <ветка>\n"
        % (name, root, "\n".join("  · " + r for r in reasons)))
