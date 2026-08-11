"""Коммит тумблера, переключённого пультом (решение владельца 2026-08-11).

Почему это вообще нужно. `payments.enabled`, `funnel_gate`, `honesty_mode` —
рантайм-тумблеры, но живут они в `settings.yaml`, который лежит под git и
является ДЕПЛОЙ-ПУТЁМ гардиана. Пульт правит этот файл командой владельца, и
до сих пор правка оставалась незакоммиченной: живое дерево становилось грязным
навсегда. 11.08 из-за этого проверка worktree в ops_watchdog простояла красной
1669 циклов, и приехавший следом недеплоенный код второго алерта уже не дал бы —
`alerted` подавлял повтор. Утверждение «грязное дерево = недеплоенный код»
переставало быть правдой ровно там, где оно и нужно.

Рассматривался вариант вынести тумблеры в файл вне git (как `requisites.yaml`).
Отвергнут по направлению отказа: там забывание НЕВИДИМО (дерево чисто в любом
случае), а здесь — громко (сторож краснеет). Плюс снимок `.versions` и стартовый
fail-safe покрывают ровно `CONFIG_FILES`, и тумблер вне их списка откатывался бы
отдельно от структуры, давая комбинацию, которой никогда не существовало.

Три предохранителя, и все три — про чужую работу рядом:

1. **Path-scoped.** Коммитится РОВНО один файл. Никакого `add -A`: рядом идёт
   живая сессия разработки, и коммит «заодно» утащил бы её незаконченное в транк.
2. **Только на транке.** Дерево на чужой ветке — это чья-то незакрытая сессия;
   уронить туда коммит про состояние прода значит спрятать это состояние.
3. **Отказ ГРОМКИЙ.** Тумблер к моменту коммита уже применён и уже работает.
   Промолчать о неудавшемся коммите — оставить владельца с грязным деревом, о
   котором он не знает, то есть ровно с тем состоянием, из которого выходим.

Отдельный штатный случай — конфиг ВНЕ репозитория: chatter деплоится копией
папки, и там нет ни истории, ни грязного дерева. Это молчаливый успех, а не
отказ: крик на каждом переключении приучил бы игнорировать этот канал.
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

GIT = "git"
GIT_TIMEOUT_S = 15
# Транк тот же, что сторожит ops_watchdog. Дублируется намеренно: chatter не
# импортирует scripts/ (направление зависимости было бы вывернуто), а расхождение
# двух констант означало бы, что пульт коммитит туда, куда сторож не смотрит.
TRUNK_BRANCH = "phase-4.0-unified-jarvis"

# Незавершённые операции git. Коммит в таком состоянии либо не пройдёт, либо
# пройдёт НЕ ТЕМ (закроет чужой merge нашим сообщением) — второе хуже.
_IN_PROGRESS = (
    ("MERGE_HEAD", "незавершённый merge"),
    ("CHERRY_PICK_HEAD", "незавершённый cherry-pick"),
    ("REVERT_HEAD", "незавершённый revert"),
    ("rebase-merge", "незавершённый rebase"),
    ("rebase-apply", "незавершённый rebase"),
)

_NOT_A_REPO = ("not a git repository", "не является git-репозиторием")
_NOTHING = ("nothing to commit", "no changes added", "нечего фиксировать",
            "нет изменений")


@dataclass(frozen=True)
class CommitOutcome:
    """`ok=False` обязано быть показано ВЛАДЕЛЬЦУ, а не только в лог.

    `committed=False` при `ok=True` — штатная тишина: коммитить было нечего либо
    конфиг лежит вне репозитория."""
    ok: bool
    committed: bool
    detail: str = ""


def _git_run(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess | None:
    """`None` — git не запустился вовсе (нет бинаря, таймаут). Это громкий
    случай: «не смогли спросить» неотличимо от «всё хорошо», если промолчать."""
    try:
        return subprocess.run(
            [GIT, *args], cwd=str(cwd), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=GIT_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("git %s не выполнен: %s", " ".join(args), exc)
        return None


def _tail(proc: subprocess.CompletedProcess, limit: int = 200) -> str:
    """Хвост вывода git. Хук печатает и в stderr, и в stdout — берём оба:
    владельцу нужна ПРИЧИНА, а не «что-то пошло не так»."""
    text = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    text = " ".join(text.split())
    return text[-limit:] if len(text) > limit else text


def commit_config_file(path, *, message: str, trunk: str = TRUNK_BRANCH,
                       ) -> CommitOutcome:
    """Закоммитить ОДИН файл конфига на транке. Никогда не бросает."""
    path = Path(path)
    cwd = path.parent

    top = _git_run(["rev-parse", "--show-toplevel"], cwd=cwd)
    if top is None:
        return CommitOutcome(False, False, "git не запустился (нет бинаря или таймаут)")
    if top.returncode != 0:
        err = (top.stderr or "").casefold()
        if any(marker in err for marker in _NOT_A_REPO):
            # Копия папки вне репозитория — штатно и молча.
            return CommitOutcome(True, False)
        return CommitOutcome(False, False, "git не отвечает: %s" % _tail(top))

    branch = _git_run(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)
    if branch is None or branch.returncode != 0:
        return CommitOutcome(False, False, "не удалось определить ветку дерева")
    current = (branch.stdout or "").strip()
    if current != trunk:
        where = "отсоединённый HEAD" if current == "HEAD" else "«%s»" % current
        return CommitOutcome(
            False, False,
            "дерево на %s, а не на транке «%s» — коммит про состояние прода "
            "в чужую ветку не кладу" % (where, trunk))

    gitdir = _git_run(["rev-parse", "--absolute-git-dir"], cwd=cwd)
    if gitdir is None or gitdir.returncode != 0:
        return CommitOutcome(False, False, "не удалось найти каталог .git")
    gd = Path((gitdir.stdout or "").strip())
    for marker, human in _IN_PROGRESS:
        if (gd / marker).exists():
            return CommitOutcome(False, False, "%s в дереве" % human)

    res = _git_run(["commit", "-m", message, "--", str(path)], cwd=cwd)
    if res is None:
        return CommitOutcome(False, False, "git commit не запустился")
    if res.returncode != 0:
        blob = ((res.stdout or "") + (res.stderr or "")).casefold()
        if any(marker in blob for marker in _NOTHING):
            # Значение уже такое — файл не менялся, дерево чистое.
            return CommitOutcome(True, False)
        return CommitOutcome(False, False, _tail(res))
    return CommitOutcome(True, True)
