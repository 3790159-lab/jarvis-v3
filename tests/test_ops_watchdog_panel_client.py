# -*- coding: utf-8 -*-
"""Десятая проба: клиентская панель (:8011) — С1, С2, С7 спеки.

Спека: docs/superpowers/specs/2026-08-20-client-panel-supervisor.md.

Сторожа написаны ОТ СПЕКИ, планового кода автор не видел. Это не формальность:
тест, написанный по коду, согласен с кодом по определению и молчит ровно там,
где код забыл.

Замер, ради которого всё это: 20.08 панель Ярины умерла вместе с родительской
сессией, порт 8011 не слушал никто, и ни одна из девяти проб `ops_watchdog`
про 8011 не знает вовсе. О смерти узнали от клиента.

Контракт, который эти сторожа фиксируют (спека §3, §3.1):

    ops_watchdog.probe_panel_client(snapshot) -> {"ok", "reason", "detail"}
        snapshot = {"host": str|None, "port": int, "status": int|None}
        host None            -> reason "no_bind_address"   (адрес не резолвится)
        status None          -> reason "no_response"       (порт свободен/мёртв)
        status 200           -> ok True
        иной код             -> reason "http:<код>"

    ops_watchdog._panel_client_snapshot() -> тот же снимок, и адрес в нём
        берётся ТОЙ ЖЕ функцией, которой панель биндится:
        run_panel_client.resolve_client_host / tailnet_ip.

Форма снимка выбрана по образцу соседей (`probe_worktree`,
`probe_secrets_bundle`): IO снаружи, решение — чистой функцией, потому что
проверять надо РЕШЕНИЕ, а не то, что процесс как-то ответил.
"""
from __future__ import annotations

import ast
import importlib.util as _ilu
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OPS_PATH = REPO_ROOT / "scripts" / "ops_watchdog.py"

_spec = _ilu.spec_from_file_location("ops_watchdog", OPS_PATH)
ow = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(ow)


def _launcher():
    """Модуль запускающего — ТОТ ЖЕ объект, который получит `ops_watchdog`.

    Имя модуля здесь и там обязано совпадать (`run_panel_client`), иначе
    monkeypatch в С7 патчил бы вторую копию и сторож позеленел бы на
    разошедшихся адресах — ровно на дефекте, который он ловит."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import run_panel_client
    return run_panel_client


def _snap(host="100.102.179.47", port=8011, status=200):
    return {"host": host, "port": port, "status": status}


# ---------------- С1: таблица случаев пробы ----------------

def test_a_healthy_panel_reads_as_ok():
    p = ow.probe_panel_client(_snap(status=200))
    assert p["ok"] is True, p


def test_no_answer_at_all_is_no_response():
    """Порт свободен / процесс мёртв. Это ровно тот случай, что случился
    20.08 и не был замечен ничем."""
    p = ow.probe_panel_client(_snap(status=None))
    assert p["ok"] is False, p
    assert p["reason"] == "no_response", p


def test_a_live_process_answering_badly_is_a_different_reason():
    """«Не отвечает» и «отвечает 500» чинятся по-разному: первое — про
    процесс, второе — про код внутри живого процесса. Один reason на оба
    случая утопил бы вторую новость в дедупе первой."""
    p = ow.probe_panel_client(_snap(status=500))
    assert p["ok"] is False, p
    assert p["reason"] == "http:500", p


def test_an_unresolvable_bind_address_says_so_instead_of_going_green():
    """Спека §3.1 дословно: «Если адрес не резолвится, проба обязана сказать
    `no_bind_address`, а не молча позеленеть».

    Молчаливое зелёное здесь — это слепая зона ровно там, где её и закрывают:
    адреса нет, значит панель не поднята вовсе."""
    p = ow.probe_panel_client(_snap(host=None))
    assert p["ok"] is False, p
    assert p["reason"] == "no_bind_address", p


def test_the_unresolvable_address_is_not_confused_with_a_dead_port():
    """Границы reason'ов: «некуда идти» и «сходили, тишина» — разные аварии.
    Первая чинится тайнетом, вторая — гардианом."""
    no_addr = ow.probe_panel_client(_snap(host=None))
    dead = ow.probe_panel_client(_snap(status=None))
    assert no_addr["reason"] != dead["reason"], (no_addr, dead)


def test_every_probe_verdict_carries_a_human_detail():
    """`detail` едет в текст алерта. Пустой detail = алерт без единого
    признака, по которому разбирают."""
    for snap in (_snap(status=None), _snap(status=500), _snap(host=None)):
        p = ow.probe_panel_client(snap)
        assert (p.get("detail") or "").strip(), (snap, p)


def test_the_reason_is_stable_while_the_detail_moves():
    """Дедуп сравнивает reason, а не detail. Если в reason заедет адрес,
    каждый переезд тайнета читался бы как НОВАЯ авария."""
    a = ow.probe_panel_client(_snap(host="100.102.179.47", status=None))
    b = ow.probe_panel_client(_snap(host="100.64.0.9", status=None))
    assert a["reason"] == b["reason"], (a, b)


def test_the_check_has_a_human_label():
    """Без метки владелец получит алерт со словом `panel_client` — именем
    ключа, а не сервиса."""
    assert "panel_client" in ow.LABELS, sorted(ow.LABELS)
    assert "8011" in ow.LABELS["panel_client"], ow.LABELS["panel_client"]


def test_the_check_goes_through_the_existing_debounce_and_recovery():
    """Проба обязана жить по общим правилам цикла, а не мимо них: один
    провал — молчание (законный рестарт гардианом), два — алерт, подъём — ✅."""
    down = {"panel_client": {"ok": False, "detail": "нет ответа",
                             "reason": "no_response"}}
    alerts, st = ow.evaluate({}, down)
    assert alerts == [], alerts
    alerts, st = ow.evaluate(st, down)
    assert len(alerts) == 1 and "DOWN" in alerts[0], alerts
    alerts, st = ow.evaluate(
        st, {"panel_client": {"ok": True, "detail": "HTTP 200", "reason": "ok"}})
    assert len(alerts) == 1 and "Восстановлено" in alerts[0], alerts


# ---------------- С2: состав проб — ЛИТЕРАЛЬНЫМ списком ----------------

# Список написан РУКАМИ по спеке (§0 перечисляет девять, §3 добавляет
# десятую), а не выведен из реализации. Выведенный согласен с ней по
# определению: забудет реализация — забудет и он.
TEN_CHECKS = [
    "backend",
    "bot_heartbeat",
    "chatter_guardian",
    "chatter_runner",
    "cloudflared",
    "disk",
    "panel_client",
    "restarts",
    "secrets_bundle",
    "worktree",
]


def _all_probes():
    return ow.probe_all(
        lambda p: 200,
        lambda p: (100 * 2 ** 30, 0, 50 * 2 ** 30),
        chatter_snapshot={"processes": [], "runner_beat_age": None,
                          "guardian_beat_age": None,
                          "guardian_lock_pid": None, "root": "x"},
        worktree_snapshot={"branch": "phase-4.0-unified-jarvis",
                           "dirty": [], "error": None},
        secrets_snapshot={"material": [], "bundle": None, "searched": [],
                          "error": None},
        panel_client_snapshot=_snap(),
    )


def test_the_cycle_runs_exactly_these_ten_checks():
    """В ОБЕ стороны. «Нет пропавших» ловит десятую, которую забыли
    подключить; «нет лишних» ловит одиннадцатую, приехавшую молча, — и она же
    ловит переименование, при котором старое имя осталось в LABELS."""
    got = sorted(_all_probes())
    assert got == sorted(TEN_CHECKS), (
        "состав проб разошёлся со спекой.\n  пропали: %s\n  лишние: %s"
        % (sorted(set(TEN_CHECKS) - set(got)),
           sorted(set(got) - set(TEN_CHECKS))))


def test_it_is_the_tenth_check():
    """Спека §3 называет число вслух: «Состав проб становится 10»."""
    probes = _all_probes()
    assert len(probes) == 10, sorted(probes)


def test_every_check_in_the_literal_list_has_a_label():
    """Литеральный список — не декорация: каждая проба обязана уметь
    представиться человеку."""
    missing = [c for c in TEN_CHECKS if c not in ow.LABELS]
    assert not missing, "пробы без человеческой метки: %s" % (missing,)


def test_without_a_snapshot_the_panel_check_does_not_appear_out_of_nowhere():
    """Обратная совместимость, как у трёх соседей: watchdog на старом
    окружении не имеет права слать DOWN о том, чего он не мерил."""
    probes = ow.probe_all(lambda p: 200,
                          lambda p: (100 * 2 ** 30, 0, 50 * 2 ** 30))
    assert "panel_client" not in probes, sorted(probes)


# ---------------- С7: адрес пробы и адрес бинда — ОДНА функция ----------------

def test_the_probe_address_moves_when_the_bind_resolver_moves(monkeypatch):
    """Сторож НА РАЗМЕТКУ, а не на совпадение значений сегодня.

    Спека §3.1: «проба ходит на тот адрес, на котором панель поднята, и берёт
    его из того же места, что и гардиан, — одной функцией». Два числа на одну
    вещь разойдутся ровно тогда, когда адрес тайнета сменится, — и проба
    станет красной на здоровой панели (или, хуже, зелёной на больной).

    Проверяем ПОДМЕНОЙ резолвера: если снимок пробы берёт адрес где-то ещё,
    он не сдвинется — и сторож покраснеет."""
    rpc = _launcher()
    monkeypatch.setattr(rpc, "tailnet_ip", lambda *a, **k: "100.77.77.77")
    monkeypatch.setattr(rpc, "resolve_client_host",
                        lambda *a, **k: ("203.0.113.5", None))
    assert ow._panel_client_snapshot()["host"] == "203.0.113.5"

    monkeypatch.setattr(rpc, "resolve_client_host",
                        lambda *a, **k: ("198.51.100.9", None))
    assert ow._panel_client_snapshot()["host"] == "198.51.100.9", (
        "адрес пробы не следует за резолвером бинда — значит он взят из "
        "второго места и разойдётся при переезде тайнета")


def test_a_refusing_resolver_produces_the_no_bind_address_verdict(monkeypatch):
    """Резолвер умеет отказывать (0.0.0.0 запрещён намеренно). Отказ обязан
    доехать до вердикта пробы, а не превратиться в пустую строку-адрес, по
    которой проба сходила бы неизвестно куда."""
    rpc = _launcher()
    monkeypatch.setattr(rpc, "tailnet_ip", lambda *a, **k: "")
    monkeypatch.setattr(
        rpc, "resolve_client_host",
        lambda *a, **k: (None, "адрес 0.0.0.0 открыл бы панель всей сети"))
    snap = ow._panel_client_snapshot()
    assert snap["host"] is None, snap
    assert ow.probe_panel_client(snap)["reason"] == "no_bind_address"


def test_a_vanished_tailnet_is_not_measured_on_the_loopback(monkeypatch):
    """§2.3, найдено уже на реализации и в первой редакции спеки отсутствовало.

    `resolve_client_host` при недоступном tailscale НЕ отказывает — он молча
    падает на петлю. Значит наивная проба сходит на `127.0.0.1:8011`, где
    панели нет никогда, получит тишину и объявит её мёртвой. Панель при этом
    ЖИВА на старом адресе, и гардиан снесёт здоровое.

    Правило: пустой `tailnet_ip()` при ожидаемо тайнетовом адресе — это
    `no_bind_address`, то есть «не могу измерить». Сторож требует, чтобы
    петля не выдавалась за адрес панели."""
    rpc = _launcher()
    monkeypatch.setattr(rpc, "tailnet_ip", lambda *a, **k: "")
    # Настоящее поведение резолвера при пропавшем тайнете — НЕ отказ, а петля.
    monkeypatch.setattr(rpc, "resolve_client_host",
                        lambda *a, **k: ("127.0.0.1", None))
    snap = ow._panel_client_snapshot()
    assert snap["host"] != "127.0.0.1", (
        "проба собралась мерить панель на петле, где её нет по построению "
        "(§3.1): тишина оттуда будет прочитана как смерть живой панели")
    assert ow.probe_panel_client(snap)["reason"] == "no_bind_address", snap


def test_a_present_tailnet_is_still_measured_normally(monkeypatch):
    """ГРАНИЦА к предыдущему: правило §2.3 не имеет права выключить пробу
    вовсе. Тайнет на месте — меряем как обычно, иначе десятая проверка станет
    вечным `no_bind_address` и не заметит ни одной настоящей смерти."""
    rpc = _launcher()
    monkeypatch.setattr(rpc, "tailnet_ip", lambda *a, **k: "100.102.179.47")
    monkeypatch.setattr(rpc, "resolve_client_host",
                        lambda *a, **k: ("100.102.179.47", None))
    assert ow._panel_client_snapshot()["host"] == "100.102.179.47"


def _module_functions() -> dict:
    """Все функции модуля по имени — граф зовущих строим по ним."""
    text = OPS_PATH.read_text(encoding="utf-8")
    return {n.name: n
            for n in ast.walk(ast.parse(text))
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _names_used(node) -> set:
    """Имена, которые функция УПОМИНАЕТ: и `foo()`, и `mod.foo()`.

    Берём имена из AST, а не подстроки из текста: комментарий, объясняющий,
    почему второго резолвера быть не должно, не имеет права краснить сторож
    (сообщение — данные, а не код)."""
    out = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            out.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            out.add(sub.attr)
    return out


def _string_constants(node) -> list:
    """Строковые литералы функции БЕЗ её докстринга."""
    body = list(node.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(
            body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]
    out = []
    for stmt in body:
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                out.append(sub.value)
    return out


SNAPSHOT = "_panel_client_snapshot"


def _reachable_from(start: str = SNAPSHOT) -> dict:
    """Функции модуля, до которых снимок дотягивается по цепочке вызовов.

    Именно ЦЕПОЧКА, а не один исходник: вынести обращение к резолверу в
    отдельную `panel_client_bind_host()` — законная факторизация, и сторож,
    который грепает тело снимка на имя резолвера, покраснел бы от того, что
    код стал лучше. Сторож, краснеющий на улучшении, приучает себя
    отключать."""
    funcs = _module_functions()
    assert start in funcs, (
        "в scripts/ops_watchdog.py нет функции %s — снимок для десятой пробы "
        "не собирается ничем" % start)
    seen, queue = {}, [start]
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        node = funcs.get(name)
        if node is None:
            continue
        seen[name] = node
        for used in _names_used(node):
            if used in funcs and used not in seen:
                queue.append(used)
    return seen


RESOLVER_NAMES = ("resolve_client_host", "tailnet_ip")


def _address_producers() -> dict:
    """Функции модуля, которые сами вычисляют адрес бинда."""
    return {name: node for name, node in _module_functions().items()
            if _names_used(node) & set(RESOLVER_NAMES)}


def test_the_address_is_computed_in_exactly_one_place():
    """Статическая половина С7, переписанная по существу требования.

    Спека §3.1 запрещает не «резолвер вне снимка», а ВТОРОЕ вычисление
    адреса: два места разойдутся ровно тогда, когда адрес тайнета сменится.
    Поэтому утверждение — про число мест во ВСЁМ модуле, а не про текст одной
    функции. Одна функция вместо другой — не дефект; две вместо одной — тот
    самый дефект."""
    producers = _address_producers()
    assert producers, (
        "ни одна функция ops_watchdog не спрашивает адрес у общего резолвера "
        "(%s) — значит проба берёт его откуда-то ещё"
        % ", ".join(RESOLVER_NAMES))
    assert len(producers) == 1, (
        "адрес бинда вычисляется в %d местах: %s. Два числа на одну вещь "
        "разойдутся при переезде тайнета" % (len(producers), sorted(producers)))


def test_the_probe_reaches_that_one_place_by_calling_it():
    """Продолжение: единственное место обязано быть ТЕМ, до которого снимок
    доходит. Иначе резолвер зовёт кто-то посторонний, а проба по-прежнему
    считает адрес сама."""
    producer = next(iter(_address_producers()))
    reachable = _reachable_from()
    assert producer in reachable, (
        "%s не дотягивается до %s по цепочке вызовов: дошли только до %s"
        % (SNAPSHOT, producer, sorted(reachable)))


def test_nothing_on_the_way_to_the_probe_hardcodes_an_address():
    """Реализация могла бы звать резолвер И держать запасной литерал на
    случай отказа — тогда поведенческий сторож
    (`test_the_probe_address_moves_when_the_bind_resolver_moves`) зелен, а
    панель молча проверяется по петле, где её нет никогда.

    Смотрим ЛИТЕРАЛЫ-СТРОКИ, а не текст: докстринг, объясняющий, почему
    петля не годится, — объяснение, а не адрес."""
    bad = {}
    for name, node in _reachable_from().items():
        hits = [s for s in _string_constants(node)
                if re.search(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", s)]
        if hits:
            bad[name] = hits
    assert not bad, (
        "на пути к пробе зашиты адреса-литералы: %s" % bad)


def test_the_probe_does_not_reuse_the_backend_base_url():
    """`BASE_URL` — это 127.0.0.1:8010, адрес ДРУГОГО процесса. Панели на
    петле нет вовсе (§3.1), поэтому проба через BASE_URL была бы красной на
    здоровой панели, а по её порту на петле — красной всегда.

    Проверяется по ВСЕЙ цепочке, а не по одной функции: вынести поход в
    хелпер и потерять адрес там — тот же дефект, только на шаг дальше."""
    users = [name for name, node in _reachable_from().items()
             if "BASE_URL" in _names_used(node)]
    assert not users, (
        "проба панели ходит через BASE_URL бэкенда: %s" % sorted(users))


def test_the_launcher_still_exposes_the_resolver_the_probe_leans_on():
    """ГРАНИЦА: С7 можно «выполнить», переименовав резолвер и подвинув обе
    стороны, — и тогда сторожа выше зелены, а панель биндится непонятно чем.
    Функция обязана существовать под своим именем и отвечать парой."""
    rpc = _launcher()
    host, problem = rpc.resolve_client_host(environ={}, ip="100.102.179.47")
    assert problem is None and host == "100.102.179.47", (host, problem)
