# -*- coding: utf-8 -*-
"""Загрузчик обязан ГРОМКО отказывать на неизвестном ключе settings.yaml.

Повод — живой случай 19.08. Спека Хайку §1.1 вводит поле `classifier_model`;
арка остановлена красным гейтом Х2, поля в коде нет. Загрузчик такую строку
ПРИНИМАЛ и молча игнорировал: `load_config` отрабатывал, атрибута на настройках
не появлялось, в логе — ни слова. Владелец, которому отказ сторожа порога кэша
советовал дописать эту строку, дописал бы её, перезапустил клиента и получил
ровно прежнее поведение. Снаружи это неотличимо от сломанного сторожа.

Класс дефекта тот же, что у всех наших врущих сторожей: **настройка, которая
выглядит применённой и не применена, хуже отсутствующей — отсутствующую
видно.** Правило уже стояло на `work_hours`/`timings`/`limits` и с тем же
обоснованием в комментарии; не хватало его на верхнем уровне, в `telegram`
и в `control`.

Два способа этому сторожу стать зелёной ширмой, оба сторожатся поимённо:

  * **Список известных ключей выведен из кода** (Л5). Выведенный список по
    определению согласен с реализацией: он примет ровно то, что реализация
    читает, и промолчит ровно там, где она забыла прочитать. Поэтому список
    ЛИТЕРАЛЬНЫЙ, а Л5 сверяет его с тем, что код реально спрашивает у `raw`.
  * **Список разошёлся с продом** (Л6). Строгая проверка, отвергающая живого
    клиента, — это не строгость, а простой: клиент не поднимется, гардиан
    уйдёт в шторм рестартов. Л6 грузит ВСЕ боевые каталоги.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from chatter.config import loader as loader_mod
from chatter.config.loader import ConfigError, load_config
from tests.chatter.test_loader import SETTINGS as _LOADER_SETTINGS, _make_client

REPO = Path(__file__).resolve().parents[2]
CLIENTS = REPO / "chatter" / "clients"


def _client(tmp_path: Path, extra_yaml: str = "") -> Path:
    _make_client(tmp_path, settings=_LOADER_SETTINGS + extra_yaml)
    return tmp_path


# ── Л1. Тот самый живой случай ───────────────────────────────────────────────

def test_l1_classifier_model_is_refused_not_swallowed(tmp_path):
    """Поле из остановленной арки §1.1. До правки — принималось и терялось."""
    with pytest.raises(ConfigError) as e:
        load_config(_client(tmp_path, "\nclassifier_model: claude-sonnet-5\n"), "demo")
    msg = str(e.value)
    assert "classifier_model" in msg, "отказ обязан НАЗВАТЬ ключ, а не просто отказать"
    assert "settings.yaml" in msg


def test_l1_refusal_lists_what_is_known(tmp_path):
    """Отказ без списка известного отправляет читать исходники. Названные
    варианты — половина цены сообщения об ошибке."""
    with pytest.raises(ConfigError) as e:
        load_config(_client(tmp_path, "\nclassifer_model: x\n"), "demo")   # опечатка
    msg = str(e.value)
    assert "known:" in msg and "model" in msg and "honesty_mode" in msg


# ── Л2/Л3. Вложенные уровни, где дыра тоже была ──────────────────────────────

def test_l2_unknown_key_in_telegram(tmp_path):
    extra = "\ntelegram:\n  allowlist: [1]\n  funel_gate: true\n"
    with pytest.raises(ConfigError) as e:
        load_config(_client(tmp_path, extra), "demo")
    assert "settings.yaml.telegram" in str(e.value) and "funel_gate" in str(e.value)


def test_l3_unknown_key_in_control(tmp_path):
    extra = "\ncontrol:\n  snooze_second: 60\n"
    with pytest.raises(ConfigError) as e:
        load_config(_client(tmp_path, extra), "demo")
    assert "settings.yaml.control" in str(e.value) and "snooze_second" in str(e.value)


def test_l3_known_nested_keys_still_pass(tmp_path):
    """Строгость, отвергающая ПРАВИЛЬНЫЙ конфиг, — это простой, а не защита."""
    extra = ("\ntelegram:\n  allowlist: [1]\n  denylist: [2]\n  funnel_gate: true\n"
             "control:\n  snooze_seconds: 60\n  auto_reload: true\n"
             "  classifier_enabled: false\n")
    cfg = load_config(_client(tmp_path, extra), "demo")
    assert cfg.settings.telegram.funnel_gate is True
    assert cfg.settings.control.snooze_seconds == 60


# ── Л4. Опечатка называется опечаткой, а не «нет обязательного поля» ─────────

def test_l4_typo_is_reported_as_typo_not_as_missing(tmp_path):
    """`modell:` вместо `model:` — это ОДНА ошибка. Сообщение «missing required
    key 'model'» заставило бы искать пропавшую строку, которая на месте."""
    settings = _LOADER_SETTINGS.replace("model: claude-haiku-4-5", "modell: claude-haiku-4-5", 1)
    _make_client(tmp_path, settings=settings)
    with pytest.raises(ConfigError) as e:
        load_config(tmp_path, "demo")
    assert "modell" in str(e.value), f"опечатка не названа: {e.value}"


# ── Л5. Список НЕ выведен из кода, но и не разошёлся с ним ───────────────────

def test_l5_known_list_matches_what_the_loader_actually_reads():
    """Сверяет ЛИТЕРАЛЬНЫЙ список с тем, что код спрашивает у `raw`.

    Граница поиска — именно `raw`, а не `tg_raw`/`c_raw`: у вложенных блоков
    свои списки. Отрицательный lookbehind нужен ровно за этим — без него
    `tg_raw.get("denylist")` попал бы в верхний уровень и тест бы врал.
    """
    src = Path(loader_mod.__file__).read_text(encoding="utf-8")
    pats = [r'(?<![\w])raw\.get\(\s*"([^"]+)"',
            r'(?<![\w])raw\[\s*"([^"]+)"\s*\]',
            r'_require\(\s*raw,\s*"([^"]+)"',
            r'_optional_mapping\(\s*raw,\s*"([^"]+)"']
    read = set()
    for p in pats:
        read |= set(re.findall(p, src))
    known = set(loader_mod._SETTINGS_KEYS)
    assert read - known == set(), (
        f"загрузчик читает ключи, которых нет в _SETTINGS_KEYS: {sorted(read - known)} "
        f"— такой ключ примут молча, ровно как раньше принимали classifier_model")
    assert known - read == set(), (
        f"в _SETTINGS_KEYS есть ключи, которых загрузчик не читает: "
        f"{sorted(known - read)} — их примут и потеряют")


# ── Л6. Строгость не ломает прод ─────────────────────────────────────────────

@pytest.mark.parametrize("slug", sorted(p.name for p in CLIENTS.iterdir()
                                        if (p / "settings.yaml").exists()))
def test_l6_every_production_client_still_loads(slug):
    """Клиент, который перестал подниматься из-за нашей строгости, — это
    авария, а не защита: гардиан уйдёт в шторм рестартов, и виноват будет
    сторож."""
    load_config(CLIENTS, slug)


def test_l6_no_production_settings_file_has_an_unknown_key():
    """То же самое, но прямым сравнением — чтобы падение называло КЛЮЧ, а не
    только «клиент не грузится»."""
    known = set(loader_mod._SETTINGS_KEYS)
    for p in sorted(CLIENTS.glob("*/settings.yaml")):
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        unknown = sorted(set(raw) - known)
        assert not unknown, f"{p.parent.name}: неизвестные ключи {unknown}"
