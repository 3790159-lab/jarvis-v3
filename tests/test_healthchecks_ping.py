"""Сторож метки dead-man-пинга.

Повод (живой инцидент 2026-07-31): `healthchecks_ping.ps1` писал метку через
`Get-Date -UFormat %s`, а в PowerShell 5.1 это считает эпоху от ЛОКАЛЬНОГО
времени. В Киеве (UTC+3) метка уезжала на 10800 с в БУДУЩЕЕ: записано
1785467032 (06:03:52) при реальном времени запуска 03:03:51, то есть возраст
последнего пинга получался ОТРИЦАТЕЛЬНЫМ.

Почему тест поведенческий, а не текстовый: урок P16-а — тест, читающий текст
скрипта, был зелёным, пока Планировщик молча отвергал триггер. Поэтому здесь
мы ВЫЧИСЛЯЕМ то самое выражение, которое реально уходит в прод, живым
powershell'ом и сверяем с часами Python. Текстовая проверка оставлена только
как дешёвая страховка от возврата заведомо сломанного API.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "healthchecks_ping.ps1"

# Допуск: метка пишется в тот же миг, что и замер. 120 с с запасом кроют
# медленный старт powershell и загруженную машину, но НИКАК не кроют сдвиг
# часового пояса — минимальный ненулевой сдвиг равен 1800 с (UTC+00:30).
TOLERANCE_S = 120


def _script_text() -> str:
    return SCRIPT.read_text(encoding="utf-8-sig")


def _stamp_expression() -> str:
    """Достаёт из скрипта выражение, которым пишется метка."""
    for line in _script_text().splitlines():
        if "healthchecks_last.txt" in line:
            continue
        if "Set-Content" in line and "$stamp" in line:
            m = re.search(r"-Value\s+\((.+?)\)\s+-Encoding", line)
            assert m, f"не разобрал строку записи метки: {line!r}"
            return m.group(1)
    raise AssertionError("в скрипте не найдена запись метки через Set-Content")


def _code_lines() -> list[str]:
    """Только исполняемые строки. Комментарии исключены намеренно: в скрипте
    сломанное API ЦИТИРУЕТСЯ в предупреждении «так не делать», и наивная
    проверка по всему тексту падала бы на собственной документации."""
    out, in_block = [], False
    for line in _script_text().splitlines():
        s = line.strip()
        if s.startswith("<#"):
            in_block = True
        if in_block:
            if s.endswith("#>"):
                in_block = False
            continue
        code = line.split("#", 1)[0]
        if code.strip():
            out.append(code)
    return out


def test_stamp_does_not_use_localtime_uformat():
    """Дешёвая страховка: сломанное API не должно вернуться в КОД."""
    offenders = [ln for ln in _code_lines() if "-UFormat %s" in ln]
    assert not offenders, (
        "Get-Date -UFormat %s в PowerShell 5.1 считает эпоху от локального "
        f"времени — метка уедет на смещение часового пояса: {offenders}"
    )


@pytest.mark.skipif(sys.platform != "win32", reason="powershell-скрипт, только Windows")
@pytest.mark.skipif(shutil.which("powershell") is None, reason="powershell не найден")
def test_stamp_expression_yields_true_utc_epoch():
    """ГЛАВНЫЙ тест: считаем ровно то выражение, что уходит в прод.

    На машине с ненулевым часовым поясом старое выражение проваливает эту
    проверку, новое — проходит. Это и есть разница, которую тест обязан ловить.
    """
    expr = _stamp_expression()
    before = time.time()
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", f"({expr})"],
        capture_output=True, text=True, timeout=60,
    )
    after = time.time()
    assert out.returncode == 0, f"powershell упал: {out.stderr}"

    value = int(out.stdout.strip())
    # Сверяем с ОКНОМ вокруг вызова, а не с одной точкой: между before и after
    # реально прошло время на запуск процесса.
    assert before - TOLERANCE_S <= value <= after + TOLERANCE_S, (
        f"метка {value} вне окна [{before:.0f}, {after:.0f}] ±{TOLERANCE_S}с — "
        f"похоже на сдвиг часового пояса ({(value - before) / 3600:.1f} ч)"
    )


@pytest.mark.skipif(sys.platform != "win32", reason="powershell-скрипт, только Windows")
@pytest.mark.skipif(shutil.which("powershell") is None, reason="powershell не найден")
def test_old_broken_expression_would_fail_this_test():
    """Доказательство, что тест не пустой.

    Если бы сломанное выражение проходило проверку выше, тест был бы
    бесполезен. Здесь мы явно показываем: на машине с ненулевым TZ старое API
    даёт метку ВНЕ допуска. На машине в UTC тест честно пропускается — там
    бага и не было.
    """
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         "[int]((Get-Date -UFormat %s)) - [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()"],
        capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, f"powershell упал: {out.stderr}"
    offset = int(out.stdout.strip())
    if abs(offset) <= TOLERANCE_S:
        pytest.skip("машина в UTC — расхождения между старым и новым API нет")
    assert abs(offset) > TOLERANCE_S, (
        "ожидали, что сломанное API разойдётся с UTC на смещение пояса"
    )
