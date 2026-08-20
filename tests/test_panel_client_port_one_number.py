# -*- coding: utf-8 -*-
"""С11: порт 8011 записан в ТРЁХ файлах — и обязан быть одним числом.

Спека: docs/superpowers/specs/2026-08-20-client-panel-supervisor.md.
Сторож добавлен по разбору первой редакции: §3.1 закрывает «два числа на одну
вещь» для АДРЕСА, но ровно та же вещь происходит с ПОРТОМ, и её не заметили.

Три места, и общей константы у них быть не может — языки разные:

    scripts/run_panel_client.py            DEFAULT_PORT   (на нём панель встаёт)
    scripts/panel_client_guardian_detached.ps1  -Port     (по нему решают, жива ли)
    scripts/ops_watchdog.py                <...>_PORT     (по нему меряет проба)

Расхождение здесь не даёт ошибки ни в одном месте по отдельности. Оно даёт
ровно ту картину, ради которой всё это писалось: гардиан считает панель
мёртвой (стучится не туда) и каждые 15 секунд поднимает ЖИВУЮ поверх живой,
а проба при этом зелёная. Или наоборот — проба вечно красная на здоровой
панели, и её перестают читать.

Сверка СТАТИЧЕСКАЯ и по ЛИТЕРАЛАМ. Импортировать и сравнивать значения
нельзя: PowerShell из pytest не импортируется, а сравнение двух питоновских
констант, одна из которых присвоена из другой, согласно по определению — и
промолчит про третье место, которое и разъедется.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "scripts" / "run_panel_client.py"
GUARDIAN = REPO_ROOT / "scripts" / "panel_client_guardian_detached.ps1"
WATCHDOG = REPO_ROOT / "scripts" / "ops_watchdog.py"

# Число названо в спеке вслух (§0, §1, §5): «yarina:8011».
SPEC_PORT = 8011


def _py_int_const(path: Path, predicate) -> tuple[str, int]:
    """(имя, значение) первой ПОДХОДЯЩЕЙ модульной int-константы.

    Читаем литерал из исходника, а не значение из импортированного модуля:
    предмет проверки — то, что человек напечатал в трёх файлах."""
    assert path.exists(), "нет файла %s" % path
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, int):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and predicate(target.id):
                found.append((target.id, node.value.value))
    assert found, (
        "в %s нет модульной int-константы с портом клиентской панели — "
        "значит порт живёт где-то в выражении, и сверить три места нечем"
        % path.name)
    assert len(found) == 1, (
        "в %s таких констант несколько: %s — уже два числа на одну вещь "
        "внутри одного файла" % (path.name, found))
    return found[0]


def _launcher_port() -> tuple[str, int]:
    return _py_int_const(LAUNCHER, lambda n: n == "DEFAULT_PORT")


def _watchdog_port() -> tuple[str, int]:
    # Имя константы спекой не зафиксировано, поэтому ищем по смыслу:
    # модульная константа, в имени которой есть и PANEL, и PORT.
    return _py_int_const(WATCHDOG,
                         lambda n: "PANEL" in n.upper() and n.upper().endswith("PORT"))


_BLOCK_COMMENT = re.compile(r"<#.*?#>", re.S)


def _ps_code(text: str) -> str:
    """Скрипт без комментариев — иначе номер порта, названный в шапке для
    человека, читался бы как четвёртое место."""
    text = _BLOCK_COMMENT.sub(" ", text)
    out = []
    for line in text.splitlines():
        buf, quote = [], None
        for ch in line:
            if quote:
                buf.append(ch)
                if ch == quote:
                    quote = None
                continue
            if ch in "'\"":
                quote = ch
                buf.append(ch)
                continue
            if ch == "#":
                break
            buf.append(ch)
        out.append("".join(buf))
    return "\n".join(out)


def _guardian_ports() -> set[int]:
    """Все числа, присвоенные переменной, чьё имя кончается на Port."""
    assert GUARDIAN.exists(), (
        "нет скрипта %s — сверять третье место не с чем" % GUARDIAN)
    code = _ps_code(GUARDIAN.read_text(encoding="utf-8-sig"))
    return {int(v) for _n, v in re.findall(r"\$(\w*[Pp]ort)\s*=\s*(\d{2,5})", code)}


def test_the_launcher_names_the_port_once():
    name, value = _launcher_port()
    assert value == SPEC_PORT, (
        "панель встаёт на %s, а спека называет %s" % (value, SPEC_PORT))
    assert name == "DEFAULT_PORT", name


def test_the_watchdog_names_the_same_port():
    _name, value = _watchdog_port()
    assert value == SPEC_PORT, (
        "проба ходит на порт %s, а панель встаёт на %s — проба красная на "
        "здоровой панели" % (value, SPEC_PORT))


def test_the_guardian_names_the_same_port():
    ports = _guardian_ports()
    assert ports, (
        "гардиан нигде не задаёт порт числом: либо он его не знает, либо "
        "число спрятано в выражении и сверке недоступно")
    assert ports == {SPEC_PORT}, (
        "в гардиане числа порта: %s, ожидалось ровно {%s}" % (sorted(ports), SPEC_PORT))


def test_all_three_places_carry_one_and_the_same_number():
    """Сердцевина С11. Сверка перекрёстная, а не «каждый равен 8011»:
    константу в спеке когда-нибудь поменяют, и тогда важно не то, что все
    трое равны старому числу, а то, что все трое равны ДРУГ ДРУГУ."""
    l_name, l_port = _launcher_port()
    w_name, w_port = _watchdog_port()
    g_ports = _guardian_ports()
    assert {l_port} == {w_port} == g_ports, (
        "порт клиентской панели разъехался по трём файлам:\n"
        "  run_panel_client.%s = %s (панель ВСТАЁТ здесь)\n"
        "  ops_watchdog.%s = %s (проба СТУЧИТ сюда)\n"
        "  panel_client_guardian_detached.ps1 $Port = %s (гардиан РЕШАЕТ по нему)"
        % (l_name, l_port, w_name, w_port, sorted(g_ports)))


def test_the_guardian_does_not_hardcode_a_second_port_inline():
    """Одно место на файл. Даже при верном умолчании второе такое же число,
    вписанное строкой ниже, переживёт правку параметра — и разъедется молча."""
    assert GUARDIAN.exists(), "нет скрипта %s" % GUARDIAN
    code = _ps_code(GUARDIAN.read_text(encoding="utf-8-sig"))
    hits = re.findall(r"(?<![\d.])%d(?![\d.])" % SPEC_PORT, code)
    assert len(hits) <= 1, (
        "число %d встречается в гардиане %d раз — параметр обязан быть "
        "единственным носителем порта" % (SPEC_PORT, len(hits)))
