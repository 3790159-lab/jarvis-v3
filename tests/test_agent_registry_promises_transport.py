"""Каталог не смеет обещать агента, которого нечем вызвать.

Правило владельца, 06.09.2026: `available: True` — это обещание, что агента
МОЖНО позвать. Обещание обеспечено ровно тремя способами:

* `endpoint` — HTTP-ручка живого бэкенда;
* `adapter` — адаптер из `agent_adapters` (не HTTP, но транспорт);
* явное исключение из СПИСКА НИЖЕ — агент с собственной веткой в `_call_agent`.

Списки здесь ЛИТЕРАЛЬНЫЕ, а не собранные интроспекцией. Список, который
вычисляет сам себя из кода, зеленеет на любом изменении кода и потому не
сторож. Если каталог изменился — правится этот файл, осознанно.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.agent_adapters import list_adapters  # noqa: E402
from app.services.agent_registry import AGENTS  # noqa: E402

# Агенты, которым разрешено быть доступными без `endpoint` и без `adapter`.
# Каждому — причина, проверяемая глазами, и своя ветка в `_call_agent`.
TRANSPORT_EXCEPTIONS = {
    "cowork_file_agent": (
        "своя ветка в _call_agent: мост Cowork, а при неактивном watcher — "
        "падение на file_processor, у которого endpoint есть. Замер 06.09: "
        "доходит до настоящего транспорта (HTTP 422 от боевой ручки), "
        "заглушки не отдаёт."
    ),
}

# Агенты, снятые с доступности 06.09.2026 — транспорта нет.
KNOWN_UNAVAILABLE = {
    "perplexity_researcher",
    "obsidian_writer",
    "google_drive",
}


def test_every_available_agent_has_a_way_to_be_called():
    adapters = {a["name"] for a in list_adapters()}
    offenders = []
    for aid, cfg in AGENTS.items():
        if not cfg.get("available"):
            continue
        if cfg.get("endpoint"):
            continue
        adapter = cfg.get("adapter")
        if adapter and adapter in adapters:
            continue
        if aid in TRANSPORT_EXCEPTIONS:
            continue
        offenders.append(f"{aid} (interface={cfg.get('interface')}, adapter={adapter!r})")
    assert not offenders, (
        "каталог обещает агентов, которых нечем вызвать: " + ", ".join(offenders)
    )


def test_declared_adapters_actually_exist():
    """`adapter` в каталоге обязан существовать в agent_adapters."""
    adapters = {a["name"] for a in list_adapters()}
    for aid, cfg in AGENTS.items():
        adapter = cfg.get("adapter")
        if adapter:
            assert adapter in adapters, f"{aid}: неизвестный адаптер {adapter!r}"


def test_unavailable_agents_say_why():
    """Снятый агент обязан нести причину: молчаливое снятие не отличить от опечатки."""
    for aid, cfg in AGENTS.items():
        if cfg.get("available"):
            continue
        assert cfg.get("unavailable_reason"), f"{aid}: available=False без причины"


def test_known_unavailable_set_is_the_documented_one():
    """Литеральный список. Растёт молча — перестаёт быть списком."""
    actual = {aid for aid, cfg in AGENTS.items() if not cfg.get("available")}
    assert actual == KNOWN_UNAVAILABLE, (
        f"состав снятых агентов изменился: было {sorted(KNOWN_UNAVAILABLE)}, "
        f"стало {sorted(actual)}"
    )


def test_transport_exceptions_are_still_available():
    """Исключение имеет смысл, только пока агент доступен."""
    for aid in TRANSPORT_EXCEPTIONS:
        assert AGENTS[aid]["available"] is True, (
            f"{aid} снят с доступности — убери его из TRANSPORT_EXCEPTIONS"
        )
