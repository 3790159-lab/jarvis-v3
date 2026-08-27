# -*- coding: utf-8 -*-
"""§6.5 спеки `2026-08-27-volska-panel-instance`: НАБОР ЗАДАЧ ПЛАНИРОВЩИКА ↔
НАБОР СЕКЦИЙ `panel` В РЕЕСТРЕ.

Сторожа писаны ОТ ТЕКСТА СПЕКИ ([[jarvis-guards-not-by-the-plan-author]]).

ПОЧЕМУ СТОРОЖ ВООБЩЕ НУЖЕН. §4 спеки принял решение: набор поднятых слагов
задаёт НАБОР ЗАДАЧ, а не обход реестра гардианом. Альтернатива красивее, но
трогает живой процесс, который прямо сейчас держит панель yarina, и цена
ошибки несимметрична. Плата за принятое решение — ровно этот сторож:
«разъехались — красное на суите, в день расхождения» (§4 дословно).

Расхождение бывает В ОБЕ СТОРОНЫ, и они чинятся по-разному:

* секция в реестре ЕСТЬ, задачи НЕТ — панель никто не поднимает, а лампы
  честно горят `no_instance`; выглядит как недоделанная арка;
* задача ЕСТЬ, секции НЕТ — задача каждые 15 секунд поднимает панель на
  умолчании, то есть НА ПОРТУ СОСЕДА. Это «подняли на 8011, судим по 8012»
  наоборот, и живая панель yarina гибнет от чужого гардиана.

ГДЕ БЕРЁТСЯ НАБОР ЗАДАЧ. В дереве есть ровно один литеральный список
зарегистрированных задач — `app/services/task_encoding.TASK_ENCODING_EXPECTATION`
(DEV-59, решение владельца 24.08). Он ЛИТЕРАЛЬНЫЙ намеренно: «выведенный
согласен с системой по определению и промолчит ровно там, где задачу завели и
забыли». Именно поэтому он же и годится второй половиной сверки: новая задача,
не вписанная туда, приедет в DEV-59 как `unexpected`.

🔴 ИЗВЕСТНОЕ СЛЕДСТВИЕ, НАЗВАННОЕ ЗАРАНЕЕ: список DEV-59 сегодня пинится как
«ровно 13 имён» (`tests/test_dev59_compare_tasks_states.py`). Вторая панельная
задача делает их 14, и ту таблицу придётся править ТЕМ ЖЕ коммитом. Это не
дефект сторожа: контракт парка задач — решение владельца, и он обязан узнать о
четырнадцатой задаче явно, а не обнаружить её в `unexpected`.

СЧИТАЕМ, А НЕ ПЕРЕЧИСЛЯЕМ: сверка идёт РАВЕНСТВОМ и СЧЁТОМ. Перебор «есть ли
задача для volska» слеп к слагу, которого автор сторожа не угадал, — а
завтрашний слаг и есть тот случай, ради которого сверка существует.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY = REPO_ROOT / "chatter" / "clients" / "registry.yaml"
REGISTRAR = REPO_ROOT / "scripts" / "register_panel_client_guardian.ps1"

# Имя базовой задачи — литералом: она зарегистрирована в системе прямо сейчас
# и держит живую панель yarina. Переименование её здесь было бы правкой
# ЖИВОГО процесса, а §9 спеки это запрещает.
BASE_TASK = "JarvisPanelClientGuardian"


def _panel_slugs() -> set:
    """Слаги, у которых в реестре ЕСТЬ секция `panel` — прямо из файла."""
    assert REGISTRY.exists(), "нет реестра %s" % REGISTRY
    data = yaml.safe_load(REGISTRY.read_text(encoding="utf-8")) or {}
    clients = data.get("clients") or {}
    return {str(slug) for slug, cfg in clients.items()
            if isinstance(cfg, dict) and cfg.get("panel") is not None}


def _expectation() -> dict:
    from app.services.task_encoding import TASK_ENCODING_EXPECTATION
    return dict(TASK_ENCODING_EXPECTATION)


def _guardian_tasks() -> dict:
    table = _expectation()
    return {n: v for n, v in table.items() if n.startswith(BASE_TASK)}


def _suffix_of(task: str) -> str:
    return task[len(BASE_TASK):].lower()


# ── прямая сторона: у каждой секции есть задача ────────────────────────────
def test_there_is_a_panel_section_to_match_at_all():
    """Предусловие сверки, названное вслух.

    Пустой набор секций сделал бы обе половины сверки зелёными ПО ПОСТРОЕНИЮ:
    ноль равен нулю. Такой сторож был бы зелен именно в тот день, когда арку
    откатили наполовину.
    """
    slugs = _panel_slugs()
    assert slugs, (
        "в реестре нет НИ ОДНОЙ секции `panel` — сверять набор задач не с чем, "
        "и любой её результат был бы зелёным по построению")
    assert len(slugs) >= 2, (
        "секция `panel` есть только у %s. §5.1 требует ОБЕ — иначе один слаг "
        "берёт порт из реестра, другой из умолчания" % sorted(slugs))


def test_the_number_of_guardian_tasks_equals_the_number_of_panel_sections():
    """СЧЁТ, а не перечисление имён."""
    slugs = _panel_slugs()
    tasks = _guardian_tasks()
    assert len(tasks) == len(slugs), (
        "секций `panel` в реестре %d (%s), а задач-гардианов панели %d (%s). "
        "Меньше задач — панель никто не поднимает; больше — лишняя задача "
        "поднимает панель на умолчании, то есть на порту соседа"
        % (len(slugs), sorted(slugs), len(tasks), sorted(tasks)))


def test_every_panel_slug_has_exactly_one_task_of_its_own():
    """Соответствие ВЗАИМНО-ОДНОЗНАЧНОЕ.

    Одна задача на два слага означала бы, что второй инстанс никем не
    присматривается; две на один — двух гардианов, дерущихся за один порт.
    """
    slugs = _panel_slugs()
    tasks = _guardian_tasks()
    suffixed = {t: _suffix_of(t) for t in tasks}

    named = {t: s for t, s in suffixed.items() if s}
    bare = [t for t, s in suffixed.items() if not s]

    unknown = {t: s for t, s in named.items() if s not in {x.lower() for x in slugs}}
    assert not unknown, (
        "задачи %s названы слагами, которых нет в реестре с секцией `panel`: "
        "%s. Такая задача поднимает панель клиенту, о котором реестр не знает"
        % (sorted(unknown), sorted(slugs)))

    covered = set(named.values())
    uncovered = sorted(s for s in slugs if s.lower() not in covered)
    assert len(bare) <= 1, (
        "задач с БАЗОВЫМ именем %s несколько (%s) — по имени не понять, за "
        "какой слаг отвечает каждая" % (BASE_TASK, sorted(bare)))
    assert len(uncovered) == len(bare), (
        "слаги без своей задачи: %s; задач с базовым именем: %s. Базовое имя "
        "закрывает РОВНО ОДИН слаг (тот, чья задача зарегистрирована "
        "исторически и переименованию не подлежит — §9: живую панель не "
        "трогаем)" % (uncovered, sorted(bare)))


def test_the_new_task_is_declared_with_the_same_protection_as_its_sibling():
    """Новая задача не имеет права проехать мимо контракта DEV-59.

    Задача, не вписанная в литеральную таблицу, приедет туда как `unexpected`
    — и это правильно, но узнать об этом владелец обязан ЗДЕСЬ, на суите, а не
    из ночного отчёта.
    """
    tasks = _guardian_tasks()
    assert tasks, (
        "в литеральной таблице задач нет ни одной панельной задачи — сверять "
        "нечего")
    kinds = set(tasks.values())
    assert len(kinds) == 1, (
        "панельные задачи объявлены с РАЗНЫМИ видами защиты: %s. Скрипт у них "
        "один и тот же, значит и защита одна" % tasks)


# ── регистратор: имя задачи обязано ехать за слагом ────────────────────────
def _registrar_code() -> str:
    assert REGISTRAR.exists(), "нет регистратора %s" % REGISTRAR
    text = REGISTRAR.read_text(encoding="utf-8-sig")
    text = re.sub(r"<#.*?#>", " ", text, flags=re.S)
    out = []
    for line in text.splitlines():
        out.append(line.split("#", 1)[0] if not line.strip().startswith("#") else "")
    return "\n".join(out)


def test_the_registrar_derives_the_task_name_from_the_slug():
    """ОДНА ЗАДАЧА — ОДИН КЛИЕНТ, и имя обязано это отражать.

    Пока `$TaskName` — константа, вторая регистрация с `-Slug volska` ПЕРЕТРЁТ
    задачу yarina (`Register-ScheduledTask -Force`), и живая панель останется
    без присмотра — при полностью зелёном выводе скрипта.
    """
    code = _registrar_code()
    assigns = re.findall(r"\$TaskName\s*=\s*(.+)", code)
    assert assigns, (
        "в регистраторе нет присваивания $TaskName — имя задачи взялось "
        "неизвестно откуда")
    assert any("$Slug" in a for a in assigns), (
        "имя задачи не зависит от $Slug: %s. Вторая регистрация с другим "
        "слагом ПЕРЕТРЁТ задачу первого клиента (-Force), и живая панель "
        "останется без присмотра молча" % [a.strip() for a in assigns])


def test_the_registrar_still_passes_the_slug_into_the_task():
    code = _registrar_code()
    assert "-Slug" in code, (
        "регистратор не передаёт `-Slug` в аргументы задачи: гардиан поднимет "
        "слаг по своему умолчанию, а не тот, ради которого задачу заводили")


def test_the_registrar_still_does_not_pass_a_port():
    """Четвёртой копии числа не появилось и в этой арке.

    Регистратор намеренно зовёт гардиан БЕЗ `-Port`; после переезда порта в
    реестр это тем более обязано остаться так.
    """
    code = _registrar_code()
    forced = re.findall(r"-Port\s+(\d+)", code)
    assert not forced, (
        "регистратор снова передаёт задаче номер порта: %s. Это четвёртое "
        "место, где живёт число, и оно переживёт правку реестра" % forced)


@pytest.mark.parametrize("slug", sorted(_panel_slugs()) or ["<нет секций>"])
def test_each_panel_slug_is_nameable_as_a_task(slug):
    """Слаг обязан быть пригоден для имени задачи.

    Слаг с пробелом или дефисом даёт имя, которое `schtasks` примет, а сверка
    по суффиксу разберёт неверно, — и расхождение станет невидимым.
    """
    assert re.fullmatch(r"[a-z0-9]+", slug), (
        "слаг %r не годится в имя задачи: сверка «набор задач ↔ набор секций» "
        "разбирает имя по суффиксу" % slug)
