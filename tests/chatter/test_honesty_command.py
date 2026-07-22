"""/honesty командой пульта — но ТОЛЬКО через confirm.

Владелец 2026-07-20 отверг команды пульта для тумблеров: «выключить честность
одним тапом с телефона» противоречит смыслу осознанного opt-in. 2026-07-21
решение пересмотрено с митигацией: кнопка видимая, но переключение в свободный
режим требует ЯВНОГО `/honesty free confirm`. Возврат к честному дефолту —
сразу, без подтверждения (безопасное направление чинят быстро).

Ключевое свойство, которое держат эти тесты: тап/команда БЕЗ confirm не меняет
файл. Если это сломается — честность выключается случайным тапом.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from chatter.config.yaml_edit import set_honesty_mode
from chatter.core.console import cfg_text
from chatter.storage.db import Store
from chatter.telethon_run import build_runner

SRC = Path(__file__).resolve().parents[2] / "chatter" / "clients"


def _clients(tmp_path) -> Path:
    dst = tmp_path / "clients"
    shutil.copytree(SRC, dst, ignore=shutil.ignore_patterns(".versions"))
    return dst


def _runner(clients):
    c = MagicMock()
    c.send_message = AsyncMock(return_value=MagicMock(id=1))
    return build_runner(
        client=c, clients_dir=clients, persona_slugs=["demo", "demo2"],
        store=Store(":memory:"), loop=asyncio.new_event_loop(), llm_mode="fake")


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _mode_of(path: Path) -> str:
    return yaml.safe_load(path.read_text(encoding="utf-8")).get("honesty_mode", "honest")


# --- чистый редактор yaml ---------------------------------------------------

def test_sets_mode_when_key_absent():
    src = "model: m\nlanguage: ru\n"
    assert yaml.safe_load(set_honesty_mode(src, honest=False))["honesty_mode"] \
        == "free_owner_liability"


def test_back_to_honest():
    src = "model: m\nhonesty_mode: free_owner_liability\n"
    assert yaml.safe_load(set_honesty_mode(src, honest=True))["honesty_mode"] == "honest"


def test_rewrites_commented_key_in_place_without_duplicating():
    # В шаблоне клиента ключ лежит ЗАКОММЕНТИРОВАННЫМ — второй плодить нельзя.
    src = "model: m\n# honesty_mode: free_owner_liability\n"
    out = set_honesty_mode(src, honest=False)
    assert yaml.safe_load(out)["honesty_mode"] == "free_owner_liability"
    assert out.count("honesty_mode") == 1


# --- команда пульта ---------------------------------------------------------

def test_status_without_arg_changes_nothing(tmp_path):
    clients = _clients(tmp_path)
    r = _runner(clients)
    before = (clients / "demo" / "settings.yaml").read_text(encoding="utf-8")
    out = _run(r.handle_config_command("honesty", "", language="ru"))
    assert (clients / "demo" / "settings.yaml").read_text(encoding="utf-8") == before
    assert out


def test_free_WITHOUT_confirm_does_not_touch_the_file(tmp_path):
    """САМОЕ ВАЖНОЕ свойство всей задачи: без confirm честность не выключается."""
    clients = _clients(tmp_path)
    r = _runner(clients)
    path = clients / "demo" / "settings.yaml"
    before = path.read_text(encoding="utf-8")

    out = _run(r.handle_config_command("honesty", "free", language="uk"))

    assert path.read_text(encoding="utf-8") == before, "файл изменён БЕЗ подтверждения"
    assert _mode_of(path) == "honest"
    assert "confirm" in out.casefold()          # владельцу сказали, как подтвердить


def test_free_with_confirm_switches_and_warns(tmp_path, caplog):
    clients = _clients(tmp_path)
    r = _runner(clients)
    path = clients / "demo" / "settings.yaml"

    with caplog.at_level(logging.WARNING):
        _run(r.handle_config_command("honesty", "free confirm", language="ru"))

    assert _mode_of(path) == "free_owner_liability"
    # WARNING с именем клиента — след в логе обязателен (как при загрузке конфига).
    assert any("demo" in rec.message for rec in caplog.records
               if rec.levelno >= logging.WARNING)


def test_back_to_honest_needs_no_confirm(tmp_path):
    # Возврат к безопасному дефолту — сразу: аварию чинят быстро.
    clients = _clients(tmp_path)
    r = _runner(clients)
    path = clients / "demo" / "settings.yaml"
    _run(r.handle_config_command("honesty", "free confirm", language="ru"))
    assert _mode_of(path) == "free_owner_liability"

    _run(r.handle_config_command("honesty", "on", language="ru"))

    assert _mode_of(path) == "honest"


@pytest.mark.parametrize("arg", ["bogus", "free_owner_liability", "off"])
def test_unknown_arg_is_rejected_without_change(tmp_path, arg):
    # В частности «off» — не синоним выключения честности: направление задают
    # только on/free, иначе смысл команды читается наугад.
    clients = _clients(tmp_path)
    r = _runner(clients)
    path = clients / "demo" / "settings.yaml"
    before = path.read_text(encoding="utf-8")

    _run(r.handle_config_command("honesty", arg, language="ru"))

    assert path.read_text(encoding="utf-8") == before


def test_documentation_comment_is_not_mistaken_for_the_key():
    """Регрессия (поймано этими тестами при реализации): в шаблоне клиента над
    ключом лежит ПРОЗА, начинающаяся как ключ —
        `# honesty_mode: honest (дефолт) — на «ты бот?» раскрывается честно.`
    Наивный регексп переписывал эту строку в живой ключ вместе с прозой, loader
    падал на мусорном значении, и команда молча не срабатывала (спасал откат)."""
    src = (
        "model: m\n"
        "# honesty_mode: honest (дефолт) — на «ты бот?» раскрывается честно. Снять\n"
        "#   гарантию можно только полным значением free_owner_liability.\n"
        "# honesty_mode: free_owner_liability\n"
    )
    out = set_honesty_mode(src, honest=False)
    assert yaml.safe_load(out)["honesty_mode"] == "free_owner_liability"
    assert "раскрывается честно. Снять" in out          # проза уцелела
    assert out.count("\nhonesty_mode:") == 1             # ровно один живой ключ


def test_live_key_wins_over_commented_sample():
    src = "honesty_mode: honest\n# honesty_mode: free_owner_liability\n"
    out = set_honesty_mode(src, honest=False)
    assert yaml.safe_load(out)["honesty_mode"] == "free_owner_liability"
    assert out.count("\n# honesty_mode:") == 1           # образец не тронут


# --- свободный режим: согласие честно про РЕАЛЬНОЕ поведение -----------------
# Ранняя версия текста говорила «стабильно выдаёт себя за человека». Но ЖИВОЙ
# дрил volska 2026-07-22 03:21 в free-режиме показал ОБРАТНОЕ: на «Ви бот?»
# ассистент раскрылся САМ («Так, я віртуальний асистент»). Free снимает
# ИНСТРУКЦИЮ честности (brain.py), но скрывать не учит — поведение не
# гарантировано НИ в одну сторону. Согласие обязано это назвать: обычно ведёт
# себя как человек, но может раскрыться сам, полное сокрытие не гарантируется.
# Активную инструкцию сокрытия НЕ добавляем (решение владельца окончательное).

# cfg_honesty_confirm — предупреждение ПЕРЕД выключением: это и есть текст
# согласия, он обязан называть ту же правду, что и статус после выключения.
_FREE_KEYS = ("cfg_honesty_free", "cfg_honesty_status_free", "cfg_honesty_confirm")
_CONCEALMENT_PROMISE = {
    "ru": "не раскрываюсь",
    "en": "do not disclose",
    "uk": "не розкриваюся",
}
_NO_GUARANTEE = {
    "ru": "не гарантируется",
    "en": "not guaranteed",
    "uk": "не гарантується",
}


@pytest.mark.parametrize("lang", ["ru", "en", "uk"])
@pytest.mark.parametrize("key", _FREE_KEYS)
def test_free_mode_never_promises_concealment(lang, key):
    # free НЕ обещает сокрытия и НЕ несёт активной инструкции скрывать
    text = cfg_text(key, lang).casefold()
    assert _CONCEALMENT_PROMISE[lang] not in text, (
        f"{key}/{lang} обещает сокрытие, которого код не делает")


@pytest.mark.parametrize("lang", ["ru", "en", "uk"])
@pytest.mark.parametrize("key", _FREE_KEYS)
def test_free_mode_states_concealment_is_not_guaranteed(lang, key):
    # реальность (дрил 2026-07-22): в free ассистент может раскрыться сам —
    # согласие обязано предупредить, что полное сокрытие НЕ гарантируется
    assert _NO_GUARANTEE[lang] in cfg_text(key, lang).casefold(), (
        f"{key}/{lang} не предупреждает, что полное сокрытие не гарантируется")


def test_replies_name_the_client(tmp_path):
    """Дрил 2026-07-22: '/honesty free confirm' на volska-раннере ответил
    'Чесність ВИМКНЕНА...' БЕЗ имени клиента, а соседний тап карточки дал
    хардкод '✅ Залишено Ані' — владелец решил, что тумблер лёг в Аню.
    Статус и оба подтверждения обязаны называть клиента."""
    clients = _clients(tmp_path)
    r = _runner(clients)

    assert "demo" in _run(r.handle_config_command("honesty", "", language="uk"))
    assert "demo" in _run(r.handle_config_command("honesty", "free confirm", language="uk"))
    assert "demo" in _run(r.handle_config_command("honesty", "", language="uk"))
    assert "demo" in _run(r.handle_config_command("honesty", "on", language="uk"))
