# -*- coding: utf-8 -*-
"""DEV-59: вызывающий сверки кодировки задач Планировщика.

До сегодняшнего дня сверку звал временный скрипт из scratchpad — то есть
сверка была, а СТОРОЖА не было: код, который живёт вне репозитория, не
переживает ни сессию, ни гейт. Здесь он заводится в репозитории и с кодом
возврата, пригодным для вызова кем угодно.

ГРАНИЦА ВСЛУХ, и она главная. `app/services/task_encoding` не знает ни про
диск, ни про Планировщик: ему ВНЕДРЯЮТ снимок и читателя. Всё импурное живёт
ЗДЕСЬ и ровно в двух функциях — `collect_snapshot` (зовёт PowerShell) и
`read_script_bytes` (читает диск). Они не пинятся и пиниться не могут: их
предмет — живая машина. Чистые `format_verdicts` и `exit_code` пинятся.

Следствие этой границы, тоже вслух: этот скрипт не отвечает за то, что
принесёт ТОТ ЖЕ файл, который реально исполняет Планировщик. Ошибка здесь
выглядит как `unreadable`, а НЕ как зелёное — сверка красная, пока признак не
доказан.

Пробы в `ops_watchdog` тут нет намеренно: она заведена отдельно (DEV-60),
потому что режет `evaluate`/`_transitions` и требует своей пары авторов.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.services.task_encoding import (  # noqa: E402  — после правки sys.path
    STATE_EXEMPT,
    STATE_OK,
    compare_tasks,
)

# Зелёными считаются РОВНО два состояния. Список литеральный и в одну сторону
# намеренно: новое состояние, добавленное завтра, обязано попасть в красные
# само собой, а не проскочить мимо чёрного списка, которого нет.
GREEN_STATES = (STATE_OK, STATE_EXEMPT)

# Имена задач парка начинаются с `Jarvis`. Фильтр стоит здесь, а не в сверке:
# сверка про кодировку, а не про то, чьи задачи в системе.
TASK_NAME_PREFIX = "Jarvis"

# Снимок снимается с ОБЪЯВЛЕННОЙ кодировкой консоли у самого дочернего
# PowerShell — тем же признаком, который эта сверка и стережёт. Иначе имена и
# аргументы с кириллицей приехали бы сюда битыми, и сверка сломалась бы ровно
# на той болезни, которую лечит.
_SNAPSHOT_PS = (
    "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
    "$ErrorActionPreference = 'Stop'; "
    "$rows = @(Get-ScheduledTask | "
    "Where-Object { $_.TaskName -like '%s*' } | ForEach-Object { "
    "  $a = @($_.Actions)[0]; "
    "  [pscustomobject]@{ "
    "    name = $_.TaskName; "
    "    Execute = $(if ($a) { $a.Execute } else { $null }); "
    "    arguments = $(if ($a) { $a.Arguments } else { $null }) } }); "
    "ConvertTo-Json -InputObject $rows -Depth 3 -Compress"
) % TASK_NAME_PREFIX


def collect_snapshot() -> list:
    """Снимок живых задач Планировщика. ИМПУРНО: зовёт PowerShell.

    Возвращает список записей `{"name", "Execute", "arguments"}` — ту самую
    однородную форму, которую сверка понимает для обоих видов защиты. Ключ
    исполнителя пишется РОВНО так, как его понимает сверка (амендмент А.4):
    иное написание она обязана читать как «исполнителя нет», и тогда все
    `ps_console`-задачи уехали бы в `execute_unknown` на ровном месте.

    Сборщик НИЧЕГО не решает: он не смотрит ни в аргументы, ни в файлы. Реши
    он хоть что-нибудь, определение признака уехало бы из-под гейта в
    непокрытый код — ровно то, за что отвергнут `chcp`.
    """
    done = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", "-Command", _SNAPSHOT_PS],
        capture_output=True,
    )
    if done.returncode != 0:
        raise RuntimeError(
            "снимок задач не снят: powershell вернул %d\n%s"
            % (done.returncode,
               done.stderr.decode("utf-8", "replace").strip()))

    text = done.stdout.decode("utf-8", "strict").strip()
    if not text:
        return []
    data = json.loads(text)
    if isinstance(data, dict):  # одна задача -> ConvertTo-Json даёт объект
        data = [data]
    return list(data)


def read_script_bytes(path: str) -> bytes:
    """СЫРЫЕ байты файла по пути. ИМПУРНО: читает диск.

    Декодирование живёт НЕ здесь, а в модуле под гейтом: правило «BOM -> utf-8,
    иначе cp1251» — часть признака, а не деталь чтения. Любое исключение
    отсюда сверка переводит в `unreadable`, наружу оно не вылетает.
    """
    return Path(path).read_bytes()


def format_verdicts(verdicts) -> list:
    """Список вердиктов -> список строк. ЧИСТО.

    По строке на вердикт, порядок строк = порядок вердиктов. В каждой строке
    обязаны быть имя задачи, состояние и `reason`: `reason` тоньше состояния,
    и без него смена одного красного на другое красное неразличима.
    """
    out = []
    for verdict in verdicts:
        line = "%-30s %-14s %s" % (
            getattr(verdict, "task", ""),
            getattr(verdict, "state", ""),
            getattr(verdict, "reason", ""))
        detail = (getattr(verdict, "detail", "") or "").strip()
        if detail:
            line = "%s  — %s" % (line, detail)
        out.append(line)
    return out


def exit_code(verdicts) -> int:
    """0 тогда и ТОЛЬКО тогда, когда каждое состояние зелёное. ЧИСТО.

    Пустой список -> 1. Сверка, не увидевшая ни одной задачи, не «прошла», а
    НЕ СОСТОЯЛАСЬ; зелёный от отсутствия ресурса — не сторож, и это уже стоило
    нам одного молчаливого сигнала.
    """
    verdicts = list(verdicts)
    if not verdicts:
        return 1
    for verdict in verdicts:
        if getattr(verdict, "state", None) not in GREEN_STATES:
            return 1
    return 0


def main(argv=None) -> int:
    """Снять снимок, сверить, напечатать, вернуть код возврата."""
    del argv  # ключей у сверки нет: она делает ровно одно и целиком
    try:
        snapshot = collect_snapshot()
    except Exception as exc:  # noqa: BLE001
        # Молчать нельзя: не снятый снимок обязан быть КРАСНЫМ и названным.
        print("снимок задач Планировщика не снят: %s (%s)"
              % (type(exc).__name__, exc))
        return 1

    verdicts = compare_tasks(snapshot, read_script=read_script_bytes)
    for line in format_verdicts(verdicts):
        print(line)

    code = exit_code(verdicts)
    red = [v.state for v in verdicts if v.state not in GREEN_STATES]
    print("")
    print("задач в снимке: %d, вердиктов: %d, красных: %d, код возврата: %d"
          % (len(snapshot), len(verdicts), len(red), code))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
