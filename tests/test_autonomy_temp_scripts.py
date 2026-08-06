# -*- coding: utf-8 -*-
"""Ш1 яруса 2, хвост первого прогона — временные register-скрипты.

Первый теневой прогон 06.08 дал 4 предложения, из них ОДНО ложное:
`register_secrets_boot_probe_TEMP.ps1` — приёмочный скрипт со своей веткой
`-Unregister`. Его таск снят НАМЕРЕННО, после зелёного ребут-теста. Предлагать
«зарегистрируй обратно» значит отменять сознательное решение — 25% шума при
гейте 20%.

Правило владельца: временный — это `_TEMP` в имени ИЛИ ветка `-Unregister`.
⚠️ Цена второго признака записана здесь честно: постоянный скрипт, который
умеет снимать свой таск, будет исключён вместе с временными. Сегодня это
ничего не меняет (на 06.08 оба признака есть ровно у одного скрипта из 15),
но если завтра постоянный скрипт обзаведётся ключом `-Unregister`, сторож
ослепнет на него молча. Поэтому причина исключения кладётся в снимок словами.
"""
from __future__ import annotations

from app.services import autonomy_detectors as det
from app.services import autonomy_snapshot as snap


def _script(**kwargs):
    base = dict(script="scripts/register_state_backup.ps1",
                task_name="JarvisStateBackup", task_present=False,
                feature_active=True)
    base.update(kwargs)
    return det.RegisterScript(**base)


# --- детектор ----------------------------------------------------------------

def test_real_missing_task_is_still_a_proposal():
    """Сторож не должен онеметь целиком: настоящая дыра обязана остаться.
    `JarvisStateBackup` на 06.08 — именно такая (Б2)."""
    found = det.detect_register_scripts_without_task(
        {"register_scripts": [_script()]})

    assert len(found) == 1
    assert found[0].subject == "JarvisStateBackup"


def test_temporary_script_is_not_a_hole():
    found = det.detect_register_scripts_without_task({"register_scripts": [
        _script(script="scripts/register_secrets_boot_probe_TEMP.ps1",
                task_name="JarvisSecretsBootProbe", temporary=True,
                temporary_reason="имя _TEMP; ветка -Unregister")]})

    assert found == []


# --- сборщик: откуда берётся признак ----------------------------------------

TEMP_TEXT = """
param(
    [switch]$Unregister
)
$TaskName = 'JarvisSecretsBootProbe'
Register-ScheduledTask -TaskName $TaskName
"""

PLAIN_TEXT = """
$TaskName = 'JarvisStateBackup'
Register-ScheduledTask -TaskName $TaskName
"""


def _collect(tmp_path, name: str, text: str):
    (tmp_path / "scripts").mkdir(exist_ok=True)
    (tmp_path / "scripts" / name).write_text(text, encoding="utf-8")
    return snap.collect_register_scripts(tmp_path, tasks={})[0]


def test_live_temp_script_carries_both_marks(tmp_path):
    """Живая форма 06.08: имя `_TEMP` и ветка `-Unregister` разом."""
    item = _collect(tmp_path, "register_secrets_boot_probe_TEMP.ps1", TEMP_TEXT)

    assert item.temporary is True
    assert "_TEMP" in item.temporary_reason and "-Unregister" in item.temporary_reason


def test_permanent_script_is_not_marked_temporary(tmp_path):
    item = _collect(tmp_path, "register_state_backup.ps1", PLAIN_TEXT)

    assert item.temporary is False and item.temporary_reason == ""


def test_temp_name_alone_is_enough(tmp_path):
    item = _collect(tmp_path, "register_probe_TEMP.ps1", PLAIN_TEXT)

    assert item.temporary is True
    assert item.temporary_reason == "имя _TEMP"


def test_unregister_switch_alone_is_enough_and_says_why(tmp_path):
    """Правило владельца — ИЛИ. Причина пишется словами именно потому, что
    этот признак слабее имени: по нему исключится и постоянный скрипт."""
    item = _collect(tmp_path, "register_state_backup.ps1", TEMP_TEXT)

    assert item.temporary is True
    assert item.temporary_reason == "ветка -Unregister"


def test_the_word_unregister_in_a_comment_is_not_a_switch(tmp_path):
    """Слово в комментарии — не ветка. Иначе исключился бы любой скрипт,
    в чьей справке упомянут `-Unregister`."""
    text = "# см. также -Unregister у соседнего скрипта\n" + PLAIN_TEXT
    item = _collect(tmp_path, "register_state_backup.ps1", text)

    assert item.temporary is False
