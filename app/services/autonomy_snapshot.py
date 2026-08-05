# -*- coding: utf-8 -*-
"""Ш1 яруса 2: сборщик снимка — единственный ГРЯЗНЫЙ слой.

Детекторы обязаны оставаться чистыми функциями, поэтому весь контакт с миром
живёт здесь: Планировщик, процессы, git, mtime метки healthchecks.

ЖЁСТКОЕ ПРАВИЛО: только чтение. Ни одного мутирующего глагола PowerShell —
`Register-`, `Set-`, `Start-`, `Stop-`, `Remove-`, `New-ScheduledTask*`,
`Unregister-`, `Disable-`, `Enable-`. Держится тестом-сторожем по исходнику:
сборщик, умеющий менять ферму, однажды её изменит.

Ожидания о ферме объявлены ДАННЫМИ (`FARM_EXPECTATIONS`), а не выведены из
самих тасков: «повтора нет» — дыра только там, где повтор ждали. Незнакомый
таск не порождает предложений вовсе (fail-closed в сторону тишины: лучше
пропустить, чем шуметь на том, о чём мы ничего не знаем).
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping

from app.services.autonomy_detectors import RegisterScript, TaskSnapshot

ROOT = Path(__file__).resolve().parents[2]

#: Что мы ждём от каждого таска фермы. Источник — разведка 06.08.
#: `repeat=True`  — обязан иметь повторяющийся триггер (сам поднимается);
#: `repeat=False` — повтор не ожидается по решению или по спеке.
FARM_EXPECTATIONS: dict[str, dict[str, Any]] = {
    "JarvisBackendGuardian": {"repeat": True},
    "JarvisChatterGuardian": {"repeat": True},
    "JarvisOpsWatchdog": {"repeat": True},
    "JarvisBotGuardian": {"repeat": True},
    "JarvisHealthchecksPing": {"repeat": True},
    "JarvisChatterCacheDigest": {"repeat": False, "why": "суточный дайджест"},
    "JarvisIgTokenRefresh": {"repeat": False, "why": "суточное обновление токена"},
    "JarvisDrillNightly": {"repeat": False, "why": "суточный регресс",
                            "refusal_codes": (2,)},
    "JarvisInfraRestartCloudflared": {"repeat": False,
                                       "why": "on-demand /Run по спеке"},
    "JarvisSniperDetached": {"repeat": False,
                              "why": "отставлен намеренно (RunPod-пивот)"},
}

#: Признак «фича спит»: если пробы нет на диске, автоматизировать нечего.
FEATURE_PROBES: dict[str, str] = {
    "register_ig_schedule_publisher.ps1": "state/ig_scheduled_posts.json",
}

HEALTHCHECKS_STAMP = "state/healthchecks_last.txt"

_TASK_NAME_RE = re.compile(r"^\s*\$TaskName\s*=\s*['\"]([A-Za-z0-9_.-]+)['\"]",
                           re.MULTILINE)

_TASKS_PS = r"""
$out = @(Get-ScheduledTask | Where-Object { $_.TaskName -like 'Jarvis*' } | ForEach-Object {
  $info = Get-ScheduledTaskInfo -TaskName $_.TaskName
  $rep = @($_.Triggers | Where-Object { $_.Repetition.Interval } |
           ForEach-Object { $_.Repetition.Interval })
  [pscustomobject]@{
    Name       = $_.TaskName
    State      = [string]$_.State
    Triggers   = @($_.Triggers | ForEach-Object { $_.CimClass.CimClassName -replace 'MSFT_Task','' })
    Repetition = if ($rep.Count) { $rep[0] } else { $null }
    LastResult = $info.LastTaskResult
  }
})
$out | ConvertTo-Json -Depth 4 -Compress
"""

_PROCESSES_PS = r"""
@(Get-Process | Select-Object -Property Id, ProcessName) |
  ConvertTo-Json -Depth 3 -Compress
"""


def parse_task_name(text: str) -> str | None:
    """Имя таска, которое зарегистрирует скрипт. `None` — скрипт таск не
    регистрирует вовсе (так устроен переключатель webhook'а Bot API)."""
    if "Register-ScheduledTask" not in text:
        return None
    match = _TASK_NAME_RE.search(text)
    return match.group(1) if match else None


def _powershell(script: str, run: Callable) -> Any:
    result = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                 capture_output=True, text=True, timeout=120)
    payload = (getattr(result, "stdout", "") or "").strip()
    if not payload:
        return []
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else [data]


def collect_tasks(run: Callable = subprocess.run) -> list[TaskSnapshot]:
    """Живые таски фермы. Только чтение."""
    out: list[TaskSnapshot] = []
    for item in _powershell(_TASKS_PS, run):
        name = item.get("Name", "")
        expectation = FARM_EXPECTATIONS.get(name, {})
        results = item.get("LastResult")
        out.append(TaskSnapshot(
            name=name,
            state=str(item.get("State", "")),
            triggers=tuple(item.get("Triggers") or ()),
            repetition_interval=item.get("Repetition"),
            expected_repetition=bool(expectation.get("repeat", False)),
            last_results=() if results is None else (int(results),),
            refusal_codes=tuple(expectation.get("refusal_codes", ())),
            note=str(expectation.get("why", "")),
        ))
    return out


def collect_register_scripts(root: Path = ROOT, *,
                             tasks: Mapping[str, TaskSnapshot] | None = None
                             ) -> list[RegisterScript]:
    """Скрипты `scripts/register_*.ps1` и судьба их тасков."""
    live = set(tasks or ())
    out: list[RegisterScript] = []
    for path in sorted((root / "scripts").glob("register_*.ps1")):
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        task_name = parse_task_name(text)
        probe = FEATURE_PROBES.get(path.name)
        active = True if probe is None else (root / probe).exists()
        out.append(RegisterScript(
            script=f"scripts/{path.name}",
            task_name=task_name,
            task_present=bool(task_name and task_name in live),
            feature_active=active,
            inactive_reason="" if active else f"нет {probe}",
        ))
    return out


def collect_git(root: Path = ROOT, *, run: Callable = subprocess.run) -> dict[str, Any]:
    """Ahead/behind относительно origin текущей ветки. Только чтение."""
    def _git(*args: str) -> str:
        result = run(["git", "-C", str(root), *args],
                     capture_output=True, text=True, timeout=30)
        return (getattr(result, "stdout", "") or "").strip()

    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    counts = _git("rev-list", "--left-right", "--count",
                  f"origin/{branch}...HEAD")
    behind, ahead = 0, 0
    parts = counts.split()
    if len(parts) == 2 and all(p.isdigit() for p in parts):
        behind, ahead = int(parts[0]), int(parts[1])
    return {"branch": branch, "ahead": ahead, "behind": behind}


def collect_stamps(root: Path = ROOT, *, now: float) -> dict[str, float | None]:
    """Возраст меток в секундах. `None` — метки нет."""
    path = root / HEALTHCHECKS_STAMP
    try:
        return {"healthchecks_age_sec": now - path.stat().st_mtime}
    except OSError:
        return {"healthchecks_age_sec": None}


def collect(root: Path = ROOT, *, now: float,
            run: Callable = subprocess.run) -> dict[str, Any]:
    """Полный снимок для детекторов. Ничего не меняет."""
    tasks = collect_tasks(run=run)
    by_name = {task.name: task for task in tasks}
    return {
        "tasks": tasks,
        "register_scripts": collect_register_scripts(root, tasks=by_name),
        "processes": _powershell(_PROCESSES_PS, run),
        "git": collect_git(root, run=run),
        "stamps": collect_stamps(root, now=now),
        "collected_at": now,
    }
