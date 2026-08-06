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

from app.services.autonomy_detectors import (RegisterScript, ServiceSnapshot,
                                             TaskSnapshot)

ROOT = Path(__file__).resolve().parents[2]

#: Манифест сервисов из клона Codex. Читаем ЧУЖУЮ песочницу и только читаем:
#: ветка Phase 0A не смержена, и на другой машине файла может не быть вовсе —
#: тогда сервисов просто нет, а не крах ночного прогона.
CODEX_MANIFEST = Path(r"C:\jarvis_codex_automation\config\jarvis_services.json")

#: Копия из чужого worktree за боевой сервис не считается. Наследство 29.07:
#: `c:/jarvis` — ПРЕФИКС `c:/jarvis_worktrees/...`, и раннер из worktree уже
#: однажды сошёл за прод. Здесь такая ошибка дала бы ложное «жив», то есть
#: ПРОПУЩЕННУЮ дыру.
FOREIGN_ROOT_MARKER = "jarvis_worktrees"

#: Нормализация слешей через `translate`, а НЕ через `.replace`: сторож
#: «модуль не зовёт пишущих методов пути» смотрит на имена атрибутов и не
#: отличает `str.replace` от `Path.replace` (переименование файла). Ослаблять
#: сторожа ради удобства — плохой размен, дешевле не занимать имя.
_SLASHES = str.maketrans("\\", "/")

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

#: Ветка `-Unregister` — это ОБЪЯВЛЕНИЕ параметра, а не слово в справке.
#: Ловить по вхождению строки значило бы исключать любой скрипт, в чьём
#: комментарии `-Unregister` упомянут.
_UNREGISTER_SWITCH_RE = re.compile(r"\[switch\]\s*\$Unregister\b")

#: Пометка автора: скрипт временный.
TEMP_NAME_MARKER = "_TEMP"

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

#: Командные строки: `Get-Process` их не отдаёт, а без них все наши сервисы —
#: одинаковые «python» и «powershell». Класс читается, не вызывается: мутация
#: CIM это `Invoke-CimMethod`, а `Invoke-` сторожу запрещён.
_PROCESS_CMDLINE_PS = r"""
@(Get-CimInstance -ClassName Win32_Process |
  Select-Object -Property ProcessId, Name, CommandLine) |
  ConvertTo-Json -Depth 3 -Compress
"""

#: `Status` — enum: `Select-Object` отдаёт его в JSON ЧИСЛОМ (4 = Running).
#: Приводим к строке на стороне PowerShell, как это уже сделано для `State`
#: тасков; разбор всё равно понимает обе формы — живой прогон 06.08 показал,
#: что выдуманная в тесте форма и реальная расходятся молча.
_WINDOWS_SERVICES_PS = r"""
@(Get-Service | ForEach-Object {
  [pscustomobject]@{ Name = $_.Name; Status = [string]$_.Status }
}) | ConvertTo-Json -Depth 3 -Compress
"""

#: `ServiceControllerStatus`: 4 = Running. Остальные коды — не «работает».
_SERVICE_RUNNING_CODE = 4


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
        reasons = []
        if TEMP_NAME_MARKER in path.stem:
            reasons.append("имя _TEMP")
        if _UNREGISTER_SWITCH_RE.search(text):
            reasons.append("ветка -Unregister")
        out.append(RegisterScript(
            script=f"scripts/{path.name}",
            task_name=task_name,
            task_present=bool(task_name and task_name in live),
            feature_active=active,
            inactive_reason="" if active else f"нет {probe}",
            temporary=bool(reasons),
            temporary_reason="; ".join(reasons),
        ))
    return out


def entrypoint_probe_key(entrypoint: str) -> str | None:
    """Имя скрипта, по которому сервис можно узнать в командной строке.

    `None` — узнать по entrypoint нельзя (`cloudflared tunnel run ...`,
    `docker-compose.yml#n8n-main`); пробу для таких даёт не entrypoint.
    """
    for token in reversed(str(entrypoint).split()):
        cleaned = token.strip("'\"").translate(_SLASHES)
        if cleaned.endswith((".py", ".ps1")):
            return cleaned.rsplit("/", 1)[-1]
    return None


def _service_is_running(status: Any) -> bool:
    """Живость службы из обеих живых форм: строки и enum-числа.

    Одной строки мало: `Select-Object -Property Status | ConvertTo-Json`
    отдаёт `4`, и сравнение с "running" молча делает работающую службу
    мёртвой — ровно это и случилось на живом прогоне 06.08.
    """
    if isinstance(status, bool):
        return False
    if isinstance(status, int):
        return status == _SERVICE_RUNNING_CODE
    return str(status).strip().lower() == "running"


def _process_is_alive(probe_key: str, processes: Any) -> bool:
    key = probe_key.lower()
    for item in processes or ():
        cmdline = (item.get("CommandLine") or "").translate(_SLASHES).lower()
        if key in cmdline and FOREIGN_ROOT_MARKER not in cmdline:
            return True
    return False


def collect_services(manifest_path: Path = CODEX_MANIFEST, *,
                     processes: Any = (),
                     windows_services: Any = ()) -> list[ServiceSnapshot]:
    """Сервисы манифеста и их живость. Только чтение.

    Три пробы и один честный отказ от пробы:
      * `windows_service` — статус из `Get-Service`;
      * `process` — имя скрипта в командной строке (`Win32_Process`);
      * `docker` — контейнеры `Get-Process` не видит, живость НЕИЗВЕСТНА;
      * `none` — entrypoint не даёт по чему искать, тоже неизвестно.
    Неизвестность едет в детектор как `None`, а не как `False`: «не проверяли»
    ≠ «мёртв».
    """
    try:
        payload = json.loads(Path(manifest_path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []

    by_service = {str(item.get("Name", "")).lower(): item.get("Status")
                  for item in (windows_services or ())}
    out: list[ServiceSnapshot] = []
    for service in payload.get("services", ()):
        owner = str(service.get("owner", ""))
        environment = str(service.get("environment", ""))
        entrypoint = str(service.get("entrypoint", ""))

        if environment == "docker":
            probe, probe_key, present = "docker", "", None
        elif owner.startswith("WindowsService:"):
            probe = "windows_service"
            probe_key = owner.split(":", 1)[1]
            status = by_service.get(probe_key.lower())
            present = None if status is None else _service_is_running(status)
        else:
            key = entrypoint_probe_key(entrypoint)
            if key is None:
                probe, probe_key, present = "none", "", None
            else:
                probe, probe_key = "process", key
                present = _process_is_alive(key, processes)

        out.append(ServiceSnapshot(
            service_id=str(service.get("id", "")),
            owner=owner,
            entrypoint=entrypoint,
            criticality=str(service.get("criticality", "")),
            production_enabled=bool(service.get("production_enabled", False)),
            probe=probe,
            probe_key=probe_key,
            process_present=present,
        ))
    return out


def collect_git(root: Path = ROOT, *, now: float,
                run: Callable = subprocess.run) -> dict[str, Any]:
    """Ahead/behind и ВОЗРАСТ самой старой неуехавшей работы. Только чтение.

    Upstream спрашивается у самого git (`@{upstream}`), а не собирается как
    `origin/<ветка>`: имя ветки и имя её удалённой пары совпадают не всегда, а
    ошибка здесь тихая — счётчики просто станут нулями, и детектор ослепнет.

    Ветка без upstream (обычное дело в worktree) даёт `None`, а НЕ ноль: ноль
    возраста означал бы «только что запушено», то есть ложное спокойствие.
    """
    def _git(*args: str) -> str:
        result = run(["git", "-C", str(root), *args],
                     capture_output=True, text=True, timeout=30)
        return (getattr(result, "stdout", "") or "").strip()

    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    upstream = _git("rev-parse", "--abbrev-ref", "@{upstream}")
    if not upstream:
        return {"branch": branch, "upstream": None, "ahead": 0, "behind": 0,
                "oldest_unpushed_age_sec": None, "oldest_unpushed_sha": None}

    behind, ahead = 0, 0
    parts = _git("rev-list", "--left-right", "--count",
                 f"{upstream}...HEAD").split()
    if len(parts) == 2 and all(p.isdigit() for p in parts):
        behind, ahead = int(parts[0]), int(parts[1])

    age, sha = None, None
    # `git log` отдаёт от нового к старому; висящую работу мерим по САМОМУ
    # СТАРОМУ коммиту — иначе каждый свежий коммит прятал бы дыру.
    lines = [line for line in _git("log", f"{upstream}..HEAD",
                                   "--format=%ct %h").splitlines() if line.strip()]
    if lines:
        oldest = lines[-1].split()
        if oldest and oldest[0].isdigit():
            age = now - int(oldest[0])
            sha = oldest[1] if len(oldest) > 1 else None
    return {"branch": branch, "upstream": upstream, "ahead": ahead,
            "behind": behind, "oldest_unpushed_age_sec": age,
            "oldest_unpushed_sha": sha}


def collect_stamps(root: Path = ROOT, *, now: float) -> dict[str, float | None]:
    """Возраст меток в секундах. `None` — метки нет."""
    path = root / HEALTHCHECKS_STAMP
    try:
        return {"healthchecks_age_sec": now - path.stat().st_mtime}
    except OSError:
        return {"healthchecks_age_sec": None}


def collect(root: Path = ROOT, *, now: float,
            run: Callable = subprocess.run,
            manifest_path: Path = CODEX_MANIFEST) -> dict[str, Any]:
    """Полный снимок для детекторов. Ничего не меняет."""
    tasks = collect_tasks(run=run)
    by_name = {task.name: task for task in tasks}
    return {
        "tasks": tasks,
        "register_scripts": collect_register_scripts(root, tasks=by_name),
        "processes": _powershell(_PROCESSES_PS, run),
        "services": collect_services(
            manifest_path,
            processes=_powershell(_PROCESS_CMDLINE_PS, run),
            windows_services=_powershell(_WINDOWS_SERVICES_PS, run)),
        "git": collect_git(root, now=now, run=run),
        "stamps": collect_stamps(root, now=now),
        "collected_at": now,
    }
