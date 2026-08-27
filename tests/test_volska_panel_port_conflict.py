# -*- coding: utf-8 -*-
"""§6.7 спеки `2026-08-27-volska-panel-instance`: ДВА ИНСТАНСА НЕ ДЕЛЯТ ПОРТ —
валидация реестра отклоняет.

Сторожа писаны ОТ ТЕКСТА СПЕКИ ([[jarvis-guards-not-by-the-plan-author]]).

ПОЧЕМУ ЭТО ОТКЛОНЯЕТСЯ ДО ЗАПУСКА, А НЕ ЛОВИТСЯ ПОТОМ. Два гардиана с одним
портом — это не «второй не поднимется». Каждый из них видит порт занятым
ЧУЖИМ процессом, сносит владельца порта и поднимает своего; следующий цикл
делает то же самое зеркально. Наружу это выглядит как две панели, каждая из
которых «то работает, то нет», и ни одна проба не назовёт причину: обе увидят
живой `/health` — просто не тот. Ровно такую же ловушку реестр уже отклоняет
для `session` и `db` («два процесса на одной Telethon-сессии = гонка за запись
.session вплоть до разлогина аккаунта»), и порт панели встаёт в тот же ряд.

ГДЕ ЖИВЁТ РЕШЕНИЕ. `chatter/core/client_registry.py` — чистый слой: парсинг +
валидация без файловой системы. Он же питает JSON-план, который читает
PowerShell-гардиан, то есть отказ доедет до той стороны, которая ПОДНИМАЕТ.

СТОРОЖА НЕ ПИНЯТ ИМЯ ПОЛЯ. Как именно порт лежит в `ClientEntry` — дело автора
кода; проверяется ПОВЕДЕНИЕ: одинаковые порты у двух включённых клиентов дают
issue и снимают обоих с запуска, разные — не дают ничего.

🔴 ВСТРЕЧНАЯ ПОЛОВИНА ОБЯЗАТЕЛЬНА. `validate`, отклоняющая всё подряд, прошла
бы прямую половину и убила бы ферму целиком. Поэтому у каждого запрета здесь
есть парный сторож на «а вот это законно».
"""
from __future__ import annotations

import pytest

from chatter.core.client_registry import parse_registry, validate

ROOT = r"C:\jarvis"
PORT_A = 8123
PORT_B = 8099


def _yaml(clients) -> str:
    """`[(slug, enabled, port|None), ...]` -> текст реестра.

    Сессии и БД у всех РАЗНЫЕ намеренно: иначе сработал бы уже существующий
    запрет на общую сессию, и сторож зеленел бы по чужой причине — то есть
    был бы зелёным по построению ровно там, где проверяет.
    """
    lines = ["clients:"]
    for slug, enabled, port in clients:
        lines.append("  %s:" % slug)
        lines.append("    enabled: %s" % ("true" if enabled else "false"))
        lines.append("    personas: [%s]" % slug)
        lines.append("    session: .secrets/%s.session" % slug)
        lines.append("    db: .secrets/%s.db" % slug)
        if port is not None:
            lines.append("    panel:")
            lines.append("      port: %d" % port)
    return "\n".join(lines) + "\n"


def _validate(clients):
    entries = parse_registry(_yaml(clients))
    return validate(entries, root=ROOT,
                    session_available=lambda s: True,
                    client_dir_exists=lambda s: True)


def _issues_for(issues, slug):
    return [i for i in issues if i.slug == slug]


# ── прямая половина: одинаковый порт = отказ ───────────────────────────────
def test_two_enabled_clients_on_one_port_are_rejected():
    runnable, issues = _validate([("volska", True, PORT_A),
                                  ("yarina", True, PORT_A)])
    assert issues, (
        "два включённых клиента объявлены на ОДНОМ порту панели (%d), а "
        "валидация не сказала ничего. Два гардиана начнут сносить панели друг "
        "друга, и обе пробы при этом увидят живой /health — просто не тот"
        % PORT_A)
    assert _issues_for(issues, "volska"), (
        "об одном из участников конфликта не сказано ничего: %s" % (issues,))
    assert _issues_for(issues, "yarina"), (
        "об одном из участников конфликта не сказано ничего: %s" % (issues,))


def test_neither_side_of_the_port_conflict_is_runnable():
    """Обоих, а не «второго».

    «Первый поднимается, второй нет» означало бы, что исход зависит от порядка
    записей в YAML: тот же реестр после пересортировки поднимал бы ДРУГУЮ
    панель. Тот же выбор уже сделан для `session` и `db`.
    """
    runnable, _issues = _validate([("volska", True, PORT_A),
                                   ("yarina", True, PORT_A)])
    slugs = {e.slug for e in runnable}
    assert "volska" not in slugs and "yarina" not in slugs, (
        "при конфликте порта к запуску допущены: %s. Кто именно уцелел, "
        "решает порядок строк в YAML — это не решение" % sorted(slugs))


def test_the_conflict_is_reported_the_same_way_regardless_of_order():
    a = _validate([("volska", True, PORT_A), ("yarina", True, PORT_A)])[1]
    b = _validate([("yarina", True, PORT_A), ("volska", True, PORT_A)])[1]
    assert {i.slug for i in a} == {i.slug for i in b}, (
        "пересортировка реестра меняет состав жалоб: %s против %s" % (a, b))


def test_the_complaint_names_the_port_the_two_share():
    """Владелец чинит по ФРАЗЕ, а не по имени переменной.

    Без числа в тексте жалоба звучит как «что-то не так с панелью», и разбор
    начинается с чтения YAML глазами.
    """
    _runnable, issues = _validate([("volska", True, PORT_A),
                                   ("yarina", True, PORT_A)])
    texts = " | ".join(i.error for i in issues)
    assert str(PORT_A) in texts, (
        "в жалобе о конфликте портов не назван сам порт %d: %r"
        % (PORT_A, texts))


def test_the_port_conflict_is_not_dressed_up_as_a_session_conflict():
    """Три запрета — три разные причины.

    Сессии и БД у клиентов сторожа РАЗНЫЕ; жалоба, говорящая про сессию,
    отправила бы владельца чинить `.secrets` там, где надо поправить одно
    число.
    """
    _runnable, issues = _validate([("volska", True, PORT_A),
                                   ("yarina", True, PORT_A)])
    texts = [i.error.lower() for i in issues]
    assert texts, "жалоб нет вовсе"
    assert any(("port" in t) or ("порт" in t) or ("panel" in t) or ("панел" in t)
               for t in texts), (
        "жалоба о конфликте ПОРТА не называет порт словами: %s" % texts)


# ── встречная половина: законное не отклоняется ────────────────────────────
def test_different_ports_are_fine():
    runnable, issues = _validate([("volska", True, PORT_A),
                                  ("yarina", True, PORT_B)])
    assert not issues, (
        "два включённых клиента на РАЗНЫХ портах отвергнуты: %s. Валидация, "
        "запрещающая законное, будет снята первой же правкой" % (issues,))
    assert {e.slug for e in runnable} == {"volska", "yarina"}, (
        "к запуску допущены не оба: %s" % sorted(e.slug for e in runnable))


def test_a_disabled_client_does_not_conflict_with_a_live_one():
    """Выключенные не валидируются вовсе — правило уже есть, здесь пин.

    Иначе отставленный `demo`, у которого секцию оставили, гасил бы живого
    соседа: клиент, которого никто не поднимает, ронял бы того, кто работает.
    """
    runnable, issues = _validate([("volska", True, PORT_A),
                                  ("demo", False, PORT_A)])
    assert not issues, (
        "выключенный клиент с тем же портом объявлен конфликтом: %s" % (issues,))
    assert {e.slug for e in runnable} == {"volska"}, (
        "включённый клиент не допущен к запуску из-за ВЫКЛЮЧЕННОГО соседа: %s"
        % sorted(e.slug for e in runnable))


def test_clients_without_a_panel_section_do_not_collide_with_each_other():
    """Отсутствие секции — не «порт None», а «панели нет».

    Наивная реализация сложит двух безпанельных клиентов в одну корзину `None`
    и объявит конфликт. Тогда ферма перестала бы запускаться ровно в том
    состоянии, в котором она живёт СЕГОДНЯ.
    """
    runnable, issues = _validate([("volska", True, None),
                                  ("yarina", True, None)])
    assert not issues, (
        "два клиента БЕЗ секции `panel` объявлены делящими порт: %s. Это "
        "сегодняшнее состояние фермы — оно обязано оставаться законным"
        % (issues,))
    assert len(runnable) == 2, sorted(e.slug for e in runnable)


def test_a_client_with_a_panel_does_not_collide_with_one_without():
    runnable, issues = _validate([("volska", True, PORT_A),
                                  ("yarina", True, None)])
    assert not issues, (
        "клиент с панелью и клиент без панели объявлены конфликтующими: %s"
        % (issues,))
    assert len(runnable) == 2, sorted(e.slug for e in runnable)


def test_three_clients_two_of_which_collide_leave_the_third_alone():
    """Авария двоих не имеет права снимать третьего.

    Тот же принцип, по которому пер-клиентные пробы живут отдельными ключами:
    беда одного клиента не глушит новость о другом.
    """
    runnable, issues = _validate([("volska", True, PORT_A),
                                  ("yarina", True, PORT_A),
                                  ("third", True, PORT_B)])
    assert {e.slug for e in runnable} == {"third"}, (
        "конфликт двоих задел третьего: к запуску допущены %s"
        % sorted(e.slug for e in runnable))
    assert not _issues_for(issues, "third"), (
        "о непричастном клиенте заведена жалоба: %s" % _issues_for(issues, "third"))


def test_the_live_registry_has_no_port_collision():
    """И, наконец, БОЕВОЙ реестр — тем же кодом.

    Сторожа на синтетике доказывают правило; эта строка доказывает, что
    правило выполняется там, где живут люди.
    """
    from pathlib import Path
    registry = Path(__file__).resolve().parent.parent / "chatter" / "clients" / "registry.yaml"
    entries = parse_registry(registry.read_text(encoding="utf-8"))
    _runnable, issues = validate(entries, root=ROOT,
                                 session_available=lambda s: True,
                                 client_dir_exists=lambda s: True)
    about_port = [i for i in issues
                  if ("port" in i.error.lower() or "порт" in i.error.lower())]
    assert not about_port, (
        "в боевом реестре два включённых клиента делят порт панели: %s"
        % about_port)
