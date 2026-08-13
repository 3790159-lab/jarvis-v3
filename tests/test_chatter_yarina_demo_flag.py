# -*- coding: utf-8 -*-
"""Демо третьего клиента (Ярина) на СУЩЕСТВУЮЩЕМ аккаунте.

Почему флагом, а не составом персон. Персона в раннере НЕ роутится по контакту:
`persona_for` отдаёт `primary_slug` всем, и переопределение появляется только
после того, как лид сам напишет `/switch` (проба 2026-08-13). Значит второй
слуг в составе означал бы «Ольга отвечает всем, Ярину видно по команде», а не
две воронки рядом. Хуже: БД одна на процесс, а панель TAMAPI фильтра по слугу
не имеет вообще — демо-диалоги Ярины попали бы в счётчики и в ленту диалогов
Ольги, то есть клиент увидел бы чужие данные.

Поэтому демо — это ВРЕМЕННАЯ подмена состава по образцу semidemo volska:
свой `CHATTER_PERSONAS`, СВОЯ база, свои логи, та же сессия аккаунта.
Контракт-тесты по исходнику `.ps1` — тот же приём, что в
`test_chatter_guardian_selfheal.py`: держим то, ЧТО будет задеплоено.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GUARDIAN = REPO_ROOT / "scripts" / "chatter_guardian_detached.ps1"
RUNNER_SCRIPT = REPO_ROOT / "scripts" / "run_yarina_demo.ps1"
FLAG = "chatter_demo_yarina.flag"


def _text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def _yarina_block() -> str:
    """Тело `if (Test-Path $yarinaFlag) { ... }` из гардиана."""
    t = _text(GUARDIAN)
    start = t.index("if (Test-Path $yarinaFlag)")
    return t[start:t.index("\n}", start)]


# ── гардиан ──────────────────────────────────────────────────────────────────

def test_guardian_knows_the_yarina_flag():
    assert FLAG in _text(GUARDIAN), (
        "гардиан не знает флага демо — поднимать Ярину было бы некому, "
        "а ручной Start-Process уже дважды умирал вместе с сессией")


def test_yarina_gets_its_own_database():
    """ГЛАВНЫЙ сторож изоляции: панель Ольги читает `.secrets/demo.db` и
    фильтра по слугу не имеет. Общая база = чужие диалоги в её панели."""
    block = _yarina_block()
    assert "yarina.db" in block, "у Ярины нет своей базы"
    assert "CHATTER_DB" in block and "demo.db" not in block.split("CHATTER_DB")[1].split("\n")[0], (
        "CHATTER_DB Ярины указывает на базу Ольги — панель покажет ей чужие лиды")


def test_yarina_keeps_the_existing_account_session():
    """Сессия — та же demo: иначе `derive_session_path` уведёт раннер на
    несуществующий `.secrets\\yarina.session`, и Telethon попросит код входа."""
    assert "demo.session" in _yarina_block()


def test_yarina_writes_to_its_own_logs():
    """Лог Ольги затирать нельзя: по нему разбирают её живые диалоги."""
    block = _yarina_block()
    assert "chatter_yarina.log" in block


def test_yarina_flag_overrides_the_volska_flag():
    """Прод-флаг Ольги стоит ВСЕГДА. Если бы блок Ярины шёл раньше, демо
    молча проигрывало бы проду и не поднималось вовсе."""
    t = _text(GUARDIAN)
    assert t.index("chatter_semidemo_volska.flag") < t.index(FLAG)


def test_guardian_announces_which_persona_it_deployed():
    """Подмена состава обязана быть ГРОМКОЙ. Молчаливая подмена = деплой,
    о котором никто не знает, и «Ольга молчит» ищут в Telegram (DEV-18)."""
    t = _text(GUARDIAN)
    assert "CHATTER_PERSONAS" in t.split("chatter guardian started")[1][:1200], (
        "гардиан не пишет в лог, чей раннер он поднял")


def _writes_to_active_yaml(text: str) -> bool:
    """Упоминание в комментарии — норма (там объясняют, откуда состав).
    ЗАПИСЬ — нет: прод-состав правит человек, а не деплой-скрипт."""
    for line in text.splitlines():
        bare = line.split("#", 1)[0]
        if "active.yaml" in bare and any(
                w in bare for w in ("Set-Content", "Out-File", "Add-Content",
                                    "New-Item", "Remove-Item", ">>", ">")):
            return True
    return False


def test_guardian_does_not_touch_the_active_roster_file():
    assert not _writes_to_active_yaml(_text(GUARDIAN))


# ── скрипт включения/отката ──────────────────────────────────────────────────

def test_demo_script_exists():
    assert RUNNER_SCRIPT.is_file()


def test_demo_script_refuses_when_the_client_is_not_onboarded_yet():
    """Флаг, поднятый до появления клиента, уронил бы Ольгу ради персоны,
    которой нет: раннер упал бы на загрузке конфига, гардиан крутил бы
    рестарты. Проверка существования каталога — до постановки флага."""
    t = _text(RUNNER_SCRIPT)
    assert "clients\\yarina" in t or "clients/yarina" in t
    assert t.index("Test-Path") < t.index("New-Item"), (
        "флаг ставится раньше проверки клиента")


def test_demo_script_has_a_revert_that_removes_the_flag():
    t = _text(RUNNER_SCRIPT)
    assert "$Revert" in t and "Remove-Item" in t


def test_demo_script_never_edits_the_active_roster():
    assert not _writes_to_active_yaml(_text(RUNNER_SCRIPT))
