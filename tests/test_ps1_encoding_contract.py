# -*- coding: utf-8 -*-
"""КЛАСС-сторож кодировки PowerShell-скриптов.

Инцидент 2026-07-25 (приёмка P16): `register_chatter_guardian.ps1` был
сохранён как UTF-8 БЕЗ BOM. Windows PowerShell 5.1 читает файл без BOM в
СИСТЕМНОЙ ANSI-кодировке (на этой машине `Encoding.Default` =
windows-1251), поэтому UTF-8 байты декодируются как кракозябры.

Само по себе это ещё не падение — кракозябры в комментариях безвредны, и
`chatter_guardian_detached.ps1` годами работал без BOM. Ломает ТИРЕ:
`—` (U+2014) в UTF-8 = `E2 80 94`, а байт `0x94` в cp1251 = `”` (U+201D).
PowerShell 5.1 считает «умные» кавычки полноценными разделителями строк,
поэтому строковый литерал закрывается посреди себя:

    throw "... триггера — самоподъём не работает"
                        ^ здесь строка кончилась для парсера

и дальше сыпется каскад `Missing closing '}'` / `missing the terminator`.
То есть одного тире в строковом литерале достаточно, чтобы деплой-скрипт
перестал запускаться.

ПОЧЕМУ BOM, А НЕ «ASCII-only»:
  * ASCII-only держится на том, что человек не наберёт тире или «ёлочки» —
    ровно та дисциплина, которая здесь и отказала; требование неисполнимое;
  * наши `.ps1` намеренно несут русские комментарии и русский вывод
    оператору — ASCII-only режет продукт;
  * BOM снимает зависимость от системной кодовой страницы целиком, и его
    одинаково понимают PS 5.1, PS 7, git и Python.

КОНТРАКТ: любой `.ps1` с не-ASCII байтами обязан начинаться с UTF-8 BOM.
Чисто-ASCII файлы BOM не требуют — читаются одинаково в любой кодировке.

⚠️ Прикладной побочный эффект: инструменты редактирования (в т.ч. Edit
ассистента) сохраняют UTF-8 БЕЗ BOM и молча срезают существующий BOM.
Этот тест — единственное, что ловит такую правку до деплоя.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BOM = b"\xef\xbb\xbf"

# Легаси со СТАРЫМИ синтаксическими ошибками, не связанными с кодировкой:
# у v2_fixed не-ASCII вообще нет, у safe_v4 BOM на месте. Список ТОЧНЫЙ:
# тест краснеет и когда сюда попадает новый файл, и когда карантинный
# наконец починили (тогда его надо отсюда убрать, а не копить).
PARSE_QUARANTINE = {
    "scripts/jarvis_operator_console_v2_fixed.ps1",
    "scripts/start_jarvis_operator_panel_safe_v4.ps1",
}


def _tracked_ps1() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "*.ps1"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def test_repo_has_ps1_files_to_check():
    """Страховка от «зелено, потому что ничего не нашли»."""
    assert len(_tracked_ps1()) > 50


def test_non_ascii_ps1_files_have_utf8_bom():
    """Главный контракт класса."""
    offenders = []
    for rel in _tracked_ps1():
        p = REPO_ROOT / rel
        if not p.is_file():
            continue
        data = p.read_bytes()
        if not any(b > 127 for b in data):
            continue
        if not data.startswith(BOM):
            offenders.append(rel)
    assert not offenders, (
        "не-ASCII .ps1 без UTF-8 BOM — PowerShell 5.1 прочитает их как "
        "windows-1251, и одного тире в строке хватит, чтобы скрипт перестал "
        f"парситься:\n  " + "\n  ".join(sorted(offenders)))


def test_no_ps1_is_utf16():
    """UTF-16 читается PowerShell'ом, но ломает git-диффы и grep по репо."""
    bad = []
    for rel in _tracked_ps1():
        p = REPO_ROOT / rel
        if p.is_file() and p.read_bytes()[:2] in (b"\xff\xfe", b"\xfe\xff"):
            bad.append(rel)
    assert not bad, f"UTF-16 .ps1 (нужен UTF-8+BOM): {bad}"


@pytest.mark.skipif(shutil.which("powershell.exe") is None,
                    reason="парсер PowerShell доступен только на Windows")
def test_every_ps1_parses():
    """Сторож ФАКТА: кодировка-контракт выше проверяет байты, а этот —
    что Windows реально скармливает файл парсеру без ошибок. Именно
    отсутствие такой проверки позволило деплою упасть молча.
    """
    ps = (
        "$ErrorActionPreference='Stop';"
        "$bad=@();"
        "foreach ($rel in (git ls-files '*.ps1')) {"
        "  $full = Join-Path (Get-Location) $rel;"
        "  if (-not (Test-Path $full)) { continue };"
        "  $e = $null;"
        "  $null = [System.Management.Automation.Language.Parser]::ParseFile("
        "    $full, [ref]$null, [ref]$e);"
        "  if ($e -and $e.Count -gt 0) { $bad += $rel }"
        "};"
        "$bad -join \"`n\""
    )
    res = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", "-Command", ps],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert res.returncode == 0, f"парсер не отработал: {res.stderr[:500]}"
    failing = {ln.strip().replace("\\", "/") for ln in res.stdout.splitlines()
               if ln.strip()}

    new_breakage = failing - PARSE_QUARANTINE
    assert not new_breakage, (
        "эти .ps1 не парсятся Windows PowerShell — запуск упадёт ParserError'ом "
        f"ДО первой строки логики:\n  " + "\n  ".join(sorted(new_breakage)))

    healed = PARSE_QUARANTINE - failing
    assert not healed, (
        "карантинные файлы починились — убери их из PARSE_QUARANTINE, иначе "
        f"список превратится в свалку: {sorted(healed)}")
