# -*- coding: utf-8 -*-
"""Сторож: у стенда ОДИН список дрил-контактов и ни одного контакта мимо него.

Ночной Д-10 06.08 не состоялся не потому, что кто-то развёл два списка, а
потому, что развелись два ответа на вопрос «кто сегодня лид»: сброс чистил
тестовый аккаунт, сценарий был прибит к контакту ручной эпохи. Списки при
этом были синхронны — сторож ловил не то.

Поэтому здесь проверяется не только совпадение списков, но и то, что КАЖДЫЙ
контакт, который стенд знает по имени (дефолт ночного прогона, контакты
поставляемых сценариев), лежит внутри этого одного списка.

$0: только чтение исходников и yaml.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "scripts"
_SCENARIOS = _ROOT / "docs" / "chatter" / "drills"

sys.path.insert(0, str(_ROOT))

from chatter.core.drill import parse_scenario  # noqa: E402


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _reset():
    return _load(_SCRIPTS / "drill_reset.py", "reset_for_sync")


def test_every_script_resolves_the_same_drill_contacts():
    """ВСЕ потребители, один список. Разошедшиеся списки — это способ однажды
    стереть переписку живого клиента или заговорить с ним от имени стенда;
    оба необратимы.

    Копия из `chatter/payments/drill_gate.py` добавлена сюда 15.08 после
    рецидива: `f63f347e` (персона yarina) обновил ДВА списка из трёх, а этот
    сторож смотрел только на `scripts/` и промолчал. Ловил расхождение
    отдельный сторож в `tests/chatter/`, то есть в другом прогоне — и держал
    его красным, пока на него не посмотрели. Сторож, охраняющий «один список»,
    обязан видеть ВСЕ копии, иначе он охраняет подмножество.
    """
    canon = _reset().DRILL_CONTACTS
    drop = _load(_SCRIPTS / "drop_phantom_obligations.py", "drop_for_sync")
    runner = _load(_SCRIPTS / "drill_runner.py", "runner_for_sync")
    nightly = _load(_SCRIPTS / "drill_nightly.py", "nightly_for_sync")
    assert drop.DRILL_CONTACTS == canon
    assert runner.drill_contacts() == canon
    assert nightly._drill_contacts() == canon

    from chatter.payments.drill_gate import DRILL_CONTACTS as payments
    assert payments == canon, (
        "копия в chatter/payments/drill_gate.py разошлась с каноном — это "
        "шлюз тестовых реквизитов, и расхождение тут стоит выдачи тестового "
        "IBAN живому клиенту либо отказа стенду в его собственных активах")


def test_nightly_default_contact_is_a_drill_contact():
    """Контакт ночного прогона — тот, кого сброс имеет право чистить, а
    автолид имеет право писать. Опечатка здесь = прогон в пустоту."""
    nightly = _load(_SCRIPTS / "drill_nightly.py", "nightly_for_default")
    assert nightly.DEFAULT_CONTACT in _reset().DRILL_CONTACTS


def test_shipped_drill_scenarios_point_at_drill_contacts():
    """Сценарий с контактом вне списка — это либо прогон в пустоту (06.08),
    либо, в авторежиме, реплика стенда живому клиенту."""
    canon = _reset().DRILL_CONTACTS
    files = sorted(_SCENARIOS.glob("*.yaml"))
    assert files, f"не найдено ни одного сценария в {_SCENARIOS}"
    for path in files:
        sc = parse_scenario(path.read_text(encoding="utf-8"))
        assert sc.contact in canon, f"{path.name}: контакт «{sc.contact}» не дрил-контакт"
