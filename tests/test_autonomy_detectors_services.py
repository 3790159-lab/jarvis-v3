# -*- coding: utf-8 -*-
"""Ш1 яруса 2 — детектор «сервис манифеста без живого процесса».

Манифест (`jarvis_services.json` клона Codex) объявляет, что ДОЛЖНО работать в
проде. Дыра — только там, где сервис объявлен рабочим (`production_enabled`) и
процесса под него в снимке нет.

Два класса молчания, каждый со своим тестом, потому что путать их нельзя:
  * сервис ЖИВ — предложение было бы ложью;
  * сервис отключён НАМЕРЕННО (`production_enabled=false`) — это решение
    владельца, а не дыра; чинить решённое ярус 2 не имеет права;
  * пробы под сервис нет (docker: `Get-Process` его не видит) — «не проверяли»
    ≠ «мёртв», иначе один прогон родил бы шесть ложных карточек разом.
"""
from __future__ import annotations

from app.services import autonomy_detectors as det


def _service(**kwargs):
    base = dict(
        service_id="telegram-bot",
        owner="WindowsScheduledTask:JarvisBotGuardian",
        entrypoint="python tools/jarvis_smart_telegram_control.py",
        criticality="critical",
        production_enabled=True,
        probe="process",
        probe_key="jarvis_smart_telegram_control.py",
        process_present=False,
    )
    base.update(kwargs)
    return det.ServiceSnapshot(**base)


def test_dead_production_service_is_a_proposal():
    """Объявлен рабочим, процесса нет — это и есть дыра."""
    found = det.detect_manifest_service_without_process({"services": [_service()]})

    assert len(found) == 1
    assert found[0].kind == "manifest_service_without_process"
    assert found[0].subject == "telegram-bot"
    assert found[0].evidence["process_present"] is False
    assert found[0].evidence["criticality"] == "critical"


def test_live_service_is_silent():
    found = det.detect_manifest_service_without_process(
        {"services": [_service(process_present=True)]})

    assert found == []


def test_deliberately_disabled_service_is_silent_even_when_dead():
    """`ollama` в манифесте `production_enabled=false` — отключён намеренно.
    Предложить его «починить» значит отменить решение владельца."""
    found = det.detect_manifest_service_without_process({"services": [_service(
        service_id="ollama",
        entrypoint="ollama serve",
        criticality="optional",
        production_enabled=False,
        process_present=False,
    )]})

    assert found == []


def test_unprobed_service_is_silent_because_unknown_is_not_dead():
    """docker-сервисы `Get-Process` не видит: `process_present=None`."""
    found = det.detect_manifest_service_without_process({"services": [_service(
        service_id="n8n-main",
        owner="DockerCompose:jarvis-n8n",
        probe="docker",
        probe_key="",
        process_present=None,
    )]})

    assert found == []


def test_action_level_is_fail_closed_until_policy_says_otherwise():
    """Перезапуск сервиса — мутация; без машиночитаемой политики уровень 4."""
    found = det.detect_manifest_service_without_process({"services": [_service()]})

    assert found[0].action_level == det.FAIL_CLOSED_LEVEL
    assert found[0].proposed_action["action"] == "restart_service"


def test_evidence_carries_no_volatile_fact():
    """Контракт хранилища: изменчивое поле в evidence = новая карточка каждый
    прогон. PID меняется при каждом рестарте — ему в наблюдении не место."""
    evidence = det.detect_manifest_service_without_process(
        {"services": [_service()]})[0].evidence

    assert "pid" not in evidence and "collected_at" not in evidence
    assert set(evidence) == {"service", "owner", "entrypoint", "criticality",
                             "probe", "probe_key", "process_present"}
