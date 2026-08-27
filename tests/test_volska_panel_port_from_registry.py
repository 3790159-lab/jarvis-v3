# -*- coding: utf-8 -*-
"""§6.1 и §6.3 спеки `2026-08-27-volska-panel-instance`: ПОРТ СЛАГА ЖИВЁТ В
РЕЕСТРЕ, и за ним обязаны поехать ОБЕ стороны — запуск панели и проба
watchdog.

Сторожа писаны ОТ ТЕКСТА СПЕКИ. Реализацию пишет другой автор в другом дереве,
его кода автор этих сторожей не видел ([[jarvis-guards-not-by-the-plan-author]]).

🔴 ГЛАВНОЕ, И ОНО ЖЕ ПРИЧИНА, ПО КОТОРОЙ ЗДЕСЬ НЕТ ЧИСЛА 8012.
Сторож, пинящий 8012 литералом, поймал бы РОВНО сегодняшнее совпадение: он
остался бы зелёным и в мире, где порт по-прежнему берётся из питоновской
константы, а в реестре просто написано то же число. Поэтому все сторожа этого
файла работают ПОДМЕНОЙ: в реестр кладутся числа, которых нет нигде в коде
(8123, 8099, 8177), и требуется, чтобы за ними поехали и запуск, и проба.
Разъезд двух источников правды виден не сегодня, а в ДЕНЬ СМЕНЫ ПОРТА — и
ловить его надо сегодня.

🔴 ВТОРОЕ: ПОРТ НЕ БЕРЁТСЯ ИЗ СНИМКА ПАНЕЛИ. В снимок панели здесь всегда
кладётся ПОДСТАВНОЙ порт (`DECOY_PORT = 7777`), которого нет ни в реестре, ни
в коде. Если проба пошла на 7777 — значит порт по-прежнему один на всю ферму,
и второй инстанс будет измеряться по адресу первого.

Контракт, который фиксируется (§3, §5.2, §5.4 спеки):

    chatter/clients/registry.yaml
        clients: {<slug>: {..., panel: {port: <int>}}}

    scripts/ops_watchdog.py
        read_roster(text)                 — секция `panel` доезжает до снимка
        _attention_snapshot(roster, panel, *, fetch=None)
        _outgoing_snapshot (roster, panel, *, fetch=None)
            -> спрашиваем ПО ПОРТУ ИЗ СЕКЦИИ, адрес — из снимка панели

    scripts/run_panel_client.py
        модульная функция «слаг -> порт из реестра»; `--port` остаётся ручным
        переопределением, `DEFAULT_PORT` — умолчанием для слага БЕЗ секции.

Имя функции спекой не названо, поэтому она ищется ПО СМЫСЛУ, а не по имени:
любая модульная функция launcher'а, в имени которой есть `port`, вызывается с
подставленным корнем и слагом. Пин по одному угаданному имени покраснел бы на
законном выборе автора кода — а это ложное красное, которое учит не смотреть.
"""
from __future__ import annotations

import importlib.util as _ilu
import inspect
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
OPS_PATH = REPO_ROOT / "scripts" / "ops_watchdog.py"
LAUNCHER_PATH = REPO_ROOT / "scripts" / "run_panel_client.py"

_spec = _ilu.spec_from_file_location("ops_watchdog_volska_port", OPS_PATH)
ow = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(ow)

# Числа, которых НЕТ в коде. Они и делают сторожа сторожем: совпадение с
# сегодняшним 8011/8012 не спасёт реализацию, которая читает не реестр.
YARINA_PORT = 8123
VOLSKA_PORT = 8099
MOVED_PORT = 8177
# Порт в снимке панели — ПОДСТАВНОЙ. Проба, взявшая его, взяла общий порт
# фермы вместо порта слага.
DECOY_PORT = 7777
HOST = "100.77.77.77"

REGISTRY_REL = "chatter/clients/registry.yaml"

# 🔴 ПОПРАВКА 1 (§3b): база volska ОБЪЯВЛЕНА и слагу НЕ равна. Синтетический
# реестр обязан это повторять. Прежняя редакция файла выводила `db:` из слага
# для ВСЕХ, то есть строила фермы, на которых расхождения не существует, — а
# слепота к расхождению и есть то, чего поправка требует не повторять. Порту
# база безразлична, но фикстура, отличающаяся от боевого реестра ровно в этом
# месте, — это допущение, которое однажды сделают снова.
DIVERGING_DBS = {"volska": ".secrets/demo.db"}


# ── реестр как ТЕКСТ, и работа с ним ЧЕРЕЗ настоящий парсер ────────────────
def _registry_yaml(clients) -> str:
    """`[(slug, enabled, port|None), ...]` -> текст `registry.yaml`.

    Собираем ТЕКСТ, а не словарь-снимок: предмет проверки — путь «строчка в
    YAML -> вердикт пробы» целиком. Снимок из моих рук проверял бы только то,
    что его скопировали.
    """
    lines = ["clients:"]
    for slug, enabled, port in clients:
        lines.append("  %s:" % slug)
        lines.append("    enabled: %s" % ("true" if enabled else "false"))
        lines.append("    personas: [%s]" % slug)
        lines.append("    session: .secrets/%s.session" % slug)
        lines.append("    db: %s"
                     % DIVERGING_DBS.get(slug, ".secrets/%s.db" % slug))
        if port is not None:
            lines.append("    panel:")
            lines.append("      port: %d" % port)
    return "\n".join(lines) + "\n"


def _root_with(tmp_path: Path, clients) -> Path:
    """Временный корень репозитория с реестром внутри."""
    root = tmp_path / "repo"
    (root / "chatter" / "clients").mkdir(parents=True, exist_ok=True)
    (root / REGISTRY_REL).write_text(_registry_yaml(clients), encoding="utf-8")
    return root


TWO_PANELS = [("volska", True, VOLSKA_PORT),
              ("yarina", True, YARINA_PORT),
              ("demo", False, None)]


def _roster(root: Path) -> dict:
    """Снимок ростера НАСТОЯЩИМ сборщиком watchdog'а из НАСТОЯЩЕГО файла."""
    snap = ow._roster_snapshot(root=root)
    assert not snap.get("error"), (
        "реестр из временного корня не прочитан вовсе (%s) — сверять нечего"
        % snap.get("error"))
    return snap


def _panel(host=HOST, port=DECOY_PORT, problem=None) -> dict:
    """Снимок панели той же формы, что отдаёт `_panel_client_snapshot`.

    Порт ПОДСТАВНОЙ намеренно: он обязан быть перебит портом из секции слага.
    """
    return {"host": host, "port": port, "status": 200, "problem": problem}


def _payload_for(path: str) -> dict:
    """Здоровое тело ручки — своё для каждого семейства."""
    if path == getattr(ow, "ATTENTION_PATH", "/ops/attention"):
        return {"open": 0, "stale_open": 0,
                "oldest_age_s": None, "oldest_wait_s": None}
    return {"pending": 0, "refused": 0, "oldest_age_s": None, "stuck": False}


class _Fetch:
    """Инъектируемый поход по сети: запоминает КУДА ходили."""

    def __init__(self):
        self.calls = []

    def __call__(self, host, port, path):
        self.calls.append({"host": host, "port": port, "path": path})
        return 200, _payload_for(path)

    def ports_of(self, slug_port_map=None):
        return sorted({c["port"] for c in self.calls})


def _collect(fn, root: Path, panel=None, fetch=None):
    """Снимок семейства настоящим сборщиком.

    `slug=` НЕ передаётся намеренно: правило арки — «есть секция `panel` в
    снимке ростера», и подсказка слагом снаружи спрятала бы ровно тот дефект,
    ради которого сторож написан.
    """
    fetch = _Fetch() if fetch is None else fetch
    snap = fn(_roster(root), _panel() if panel is None else panel, fetch=fetch)
    return snap, fetch


def _families():
    return [("escalation", ow._attention_snapshot),
            ("outgoing", ow._outgoing_snapshot)]


# ── §6.1, половина watchdog: проба идёт по порту ИЗ РЕЕСТРА ────────────────
@pytest.mark.parametrize("name,collector", _families())
def test_each_family_asks_the_port_written_in_the_registry(tmp_path, name, collector):
    """Два включённых слага, два РАЗНЫХ порта в реестре — два разных похода.

    Если реализация продолжает жить одним числом на всю ферму, оба похода
    уйдут на один порт, и это видно прямо здесь.
    """
    root = _root_with(tmp_path, TWO_PANELS)
    snap, fetch = _collect(collector, root)
    assert snap is not None, "сборщик семейства %s вернул None" % name

    by_slug = {}
    for call in fetch.calls:
        by_slug.setdefault(call["port"], []).append(call)

    assert VOLSKA_PORT in by_slug, (
        "семейство %s не сходило на порт volska (%d), написанный в реестре; "
        "ходило на: %s" % (name, VOLSKA_PORT, sorted(by_slug)))
    assert YARINA_PORT in by_slug, (
        "семейство %s не сходило на порт yarina (%d), написанный в реестре; "
        "ходило на: %s" % (name, YARINA_PORT, sorted(by_slug)))


@pytest.mark.parametrize("name,collector", _families())
def test_the_port_of_the_panel_snapshot_does_not_decide_for_a_slug(tmp_path, name, collector):
    """🔴 Порт слага перебивает общий порт фермы.

    В снимке панели лежит подставной 7777. Поход на него означает, что
    `PANEL_CLIENT_PORT` по-прежнему решает за слаг — то есть второй инстанс
    будет измеряться по адресу первого, и лампа станет фоном на здоровой
    панели.
    """
    root = _root_with(tmp_path, TWO_PANELS)
    _snap, fetch = _collect(collector, root)
    walked = [c["port"] for c in fetch.calls]
    assert DECOY_PORT not in walked, (
        "семейство %s пошло на подставной порт снимка панели (%d) — значит "
        "порт слага из реестра не читается вовсе; походы: %s"
        % (name, DECOY_PORT, walked))
    assert walked, (
        "семейство %s не сходило НИКУДА: два включённых слага с секцией "
        "`panel` обязаны быть спрошены" % name)


@pytest.mark.parametrize("name,collector", _families())
def test_moving_the_number_in_the_registry_moves_the_probe(tmp_path, name, collector):
    """СЕРДЦЕВИНА §6.1 со стороны watchdog: подмена числа в реестре.

    Совпадение чисел сегодня доказывает ноль. Доказывает — ДВИЖЕНИЕ: правим
    одну строчку YAML, и проба обязана поехать за ней.
    """
    root = _root_with(tmp_path, TWO_PANELS)
    _snap, before = _collect(collector, root)
    assert VOLSKA_PORT in [c["port"] for c in before.calls], (
        "исходный порт volska (%d) не прочитан из реестра; походы: %s"
        % (VOLSKA_PORT, [c["port"] for c in before.calls]))

    (root / REGISTRY_REL).write_text(
        _registry_yaml([("volska", True, MOVED_PORT),
                        ("yarina", True, YARINA_PORT),
                        ("demo", False, None)]), encoding="utf-8")

    _snap2, after = _collect(collector, root)
    ports = [c["port"] for c in after.calls]
    assert MOVED_PORT in ports, (
        "порт volska в реестре сменился на %d, а семейство %s по-прежнему "
        "ходит на %s — у порта ДВА источника правды, и разъедутся они в день "
        "смены порта" % (MOVED_PORT, name, ports))
    assert VOLSKA_PORT not in ports, (
        "семейство %s ходит и на старый порт %d тоже: где-то осталось второе "
        "написание числа" % (name, VOLSKA_PORT))


@pytest.mark.parametrize("name,collector", _families())
def test_the_verdict_names_the_port_it_walked_to(tmp_path, name, collector):
    """Вердикт обязан назвать АДРЕС похода.

    Без этого разбор начинается с догадки «а куда вообще ходила проба» —
    ровно то, ради чего адрес и живёт в `detail` (докстринги обеих проб).
    """
    root = _root_with(tmp_path, TWO_PANELS)
    snap, _fetch = _collect(collector, root)
    probe = ow.probe_attention if name == "escalation" else ow.probe_outgoing
    verdict = probe(snap["clients"]["volska"])
    assert verdict.get("ok") is True, (
        "здоровый инстанс volska получил не зелёное: %s" % (verdict,))
    assert str(VOLSKA_PORT) in verdict.get("detail", ""), (
        "в `detail` семейства %s нет порта %d, по которому проба ходила: %r"
        % (name, VOLSKA_PORT, verdict.get("detail")))


def test_both_families_ask_the_same_slug_on_the_same_port(tmp_path):
    """Два семейства — один адрес слага.

    Разные порты у эскалаций и у отправки означали бы два вычисления адреса
    рядом; расходятся такие пары молча ([[jarvis-two-numbers-for-one-thing]]).
    """
    root = _root_with(tmp_path, TWO_PANELS)
    _a, fa = _collect(ow._attention_snapshot, root)
    _o, fo = _collect(ow._outgoing_snapshot, root)
    att = {c["port"] for c in fa.calls}
    out = {c["port"] for c in fo.calls}
    assert att == out, (
        "эскалации ходят на порты %s, отправка — на %s: два вычисления адреса "
        "на одну вещь" % (sorted(att), sorted(out)))


# ── §6.3: адрес — от ТОГО ЖЕ резолвера, и за ним едут ОБЕ пробы ────────────
@pytest.fixture()
def resolver(monkeypatch):
    """Подменённый резолвер бинда панели в ЕДИНСТВЕННОМ общем объекте модуля.

    Подмена, а не совпадение чисел: «адрес пробы и адрес бинда — одна
    функция» иначе остаётся утверждением, которое нечем проверить.
    """
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import run_panel_client as rpc

    calls = {"resolve": 0, "ip": 0}

    def fake_ip(*a, **k):
        calls["ip"] += 1
        return HOST

    def fake_resolve(*a, **k):
        calls["resolve"] += 1
        return (HOST, None)

    monkeypatch.setattr(rpc, "tailnet_ip", fake_ip)
    monkeypatch.setattr(rpc, "resolve_client_host", fake_resolve)

    def no_network(*a, **k):
        # На живой порт не ходим НИКОГДА: панель yarina сейчас работает.
        raise OSError("сеть в сторожах закрыта")

    monkeypatch.setattr(ow.urllib.request, "urlopen", no_network)
    return calls


def test_both_families_walk_to_the_address_the_resolver_gave(tmp_path, resolver):
    """§6.3 дословно: подменив резолвер В ОДНОМ месте, сторож доказывает, что
    за ним поехали ОБЕ пробы."""
    root = _root_with(tmp_path, TWO_PANELS)
    panel = ow._panel_client_snapshot()
    assert panel is not None and panel.get("host") == HOST, (
        "снимок панели не взял адрес у подменённого резолвера: %s" % (panel,))

    _a, fa = _collect(ow._attention_snapshot, root, panel=panel)
    _o, fo = _collect(ow._outgoing_snapshot, root, panel=panel)
    hosts = {c["host"] for c in fa.calls} | {c["host"] for c in fo.calls}
    assert hosts == {HOST}, (
        "пробы пошли не на тот адрес, который дал резолвер бинда: %s"
        % (sorted(hosts),))


def test_the_resolver_is_called_once_for_the_whole_cycle(tmp_path, resolver):
    """Второй вызов резолвера рядом — это два числа на одну вещь.

    Порт переехал в реестр, адрес — нет: он по-прежнему собирается ОДИН раз
    снимком панели и делится между семействами.
    """
    root = _root_with(tmp_path, TWO_PANELS)
    panel = ow._panel_client_snapshot()
    _collect(ow._attention_snapshot, root, panel=panel)
    _collect(ow._outgoing_snapshot, root, panel=panel)
    assert resolver["resolve"] == 1, (
        "резолвер бинда позван %d раз(а) вместо одного: адрес собирается "
        "второй раз рядом" % resolver["resolve"])


def test_a_refusing_resolver_reaches_both_families_as_no_bind_address(tmp_path, monkeypatch):
    """Отказ резолвера — это «мерить нечем», а не «инстанса нет».

    Слить их значило бы соврать о ростере там, где сломан наблюдатель.
    """
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import run_panel_client as rpc
    monkeypatch.setattr(rpc, "tailnet_ip", lambda *a, **k: "")
    monkeypatch.setattr(rpc, "resolve_client_host",
                        lambda *a, **k: (None, "резолвер отказал"))
    monkeypatch.setattr(ow.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("закрыто")))

    root = _root_with(tmp_path, TWO_PANELS)
    panel = ow._panel_client_snapshot()
    for name, collector, probe in (("escalation", ow._attention_snapshot, ow.probe_attention),
                                   ("outgoing", ow._outgoing_snapshot, ow.probe_outgoing)):
        snap, _f = _collect(collector, root, panel=panel)
        verdict = probe(snap["clients"]["volska"])
        assert verdict.get("reason") == "no_bind_address", (
            "семейство %s при отказавшем резолвере сказало %r вместо "
            "`no_bind_address`: «не смогли спросить» выдано за вердикт о "
            "ростере" % (name, verdict.get("reason")))


# ── §6.1, половина launcher: за реестром едет и ЗАПУСК панели ──────────────
_MISS = object()


def _launcher():
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import run_panel_client as rpc
    return rpc


def _port_functions(mod):
    """Модульные функции, которые МОГУТ быть резолвером порта.

    Ищем по смыслу («в имени есть port»), а не по одному угаданному имени:
    спека имени не назвала, и пин по догадке краснел бы на законном выборе
    автора кода — то есть учил бы не смотреть на красное.
    """
    out = []
    for name in dir(mod):
        if name.startswith("__"):
            continue
        fn = getattr(mod, name, None)
        if not inspect.isfunction(fn):
            continue
        if getattr(fn, "__module__", None) != mod.__name__:
            continue
        if "port" not in name.lower():
            continue
        out.append((name, fn))
    return out


def _adapt_call(fn, *, slug, root, registry_path, text):
    """Позвать функцию, подставив аргументы ПО СМЫСЛУ ИМЁН параметров."""
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return _MISS
    args, kwargs = [], {}
    for pname, p in sig.parameters.items():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        low = pname.lower()
        if low in ("slug", "name", "client", "client_slug"):
            value = slug
        elif low in ("root", "repo_root", "base", "repo", "root_dir"):
            value = root
        elif "registry" in low or low in ("path", "file", "yaml_path"):
            value = registry_path
        elif low in ("text", "content", "yaml", "source"):
            value = text
        elif p.default is not p.empty:
            continue
        else:
            return _MISS
        if p.kind is p.POSITIONAL_ONLY:
            args.append(value)
        else:
            kwargs[pname] = value
    try:
        return fn(*args, **kwargs)
    except Exception:
        return _MISS


def _launcher_ports(mod, slug, root):
    """{имя функции: порт} — все кандидаты, отдавшие целое число."""
    registry_path = root / REGISTRY_REL
    text = registry_path.read_text(encoding="utf-8")
    found = {}
    for name, fn in _port_functions(mod):
        for rootval in (root, str(root)):
            value = _adapt_call(fn, slug=slug, root=rootval,
                                registry_path=registry_path, text=text)
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                found[name] = value
                break
    return found


@pytest.fixture()
def launcher_on(tmp_path, monkeypatch):
    """Launcher, смотрящий на ВРЕМЕННЫЙ корень.

    Корень подменяется и через `_ROOT`, и аргументом: часть подписей корня не
    принимает вовсе, и без подмены атрибута такая функция читала бы боевой
    реестр — а его сторожа не трогают.
    """
    mod = _launcher()
    root = _root_with(tmp_path, TWO_PANELS)
    for attr in ("_ROOT", "ROOT"):
        if hasattr(mod, attr):
            monkeypatch.setattr(mod, attr, root)
    return mod, root


def test_the_launcher_resolves_a_slug_port_from_the_registry(launcher_on):
    """§5.2: порт по умолчанию launcher берёт ИЗ РЕЕСТРА по слагу.

    Функция обязана быть модульной и чистой: её зовёт не только сам запуск, но
    и всё, что должно знать адрес слага. Порт, вычисленный внутри `main()`,
    для сверки недоступен вовсе — а «недоступно» здесь означает второй
    источник правды по построению.
    """
    mod, root = launcher_on
    found = _launcher_ports(mod, "volska", root)
    assert found, (
        "в scripts/run_panel_client.py нет модульной функции, отдающей порт "
        "слага по реестру (искали любую функцию с `port` в имени). Порт "
        "слага, вычисляемый только внутри main(), остаётся вторым источником "
        "правды: ни гардиан, ни сверка его не увидят")
    assert VOLSKA_PORT in set(found.values()), (
        "ни одна функция launcher'а не отдала порт volska из реестра (%d); "
        "получено: %s" % (VOLSKA_PORT, found))


def test_the_launcher_gives_each_slug_its_own_port(launcher_on):
    """Два слага — два числа. Один ответ на оба означает общий порт фермы."""
    mod, root = launcher_on
    volska = _launcher_ports(mod, "volska", root)
    yarina = _launcher_ports(mod, "yarina", root)
    common = set(volska) & set(yarina)
    assert common, "резолвер порта не нашёлся: %s / %s" % (volska, yarina)
    moved = [n for n in common if volska[n] == VOLSKA_PORT and yarina[n] == YARINA_PORT]
    assert moved, (
        "ни одна функция не различает слаги: volska=%s, yarina=%s, а в реестре "
        "%d и %d" % (volska, yarina, VOLSKA_PORT, YARINA_PORT))


def test_moving_the_number_in_the_registry_moves_the_launcher(launcher_on):
    """СЕРДЦЕВИНА §6.1 со стороны запуска: за правкой YAML едет launcher."""
    mod, root = launcher_on
    before = _launcher_ports(mod, "volska", root)
    names = [n for n, v in before.items() if v == VOLSKA_PORT]
    assert names, ("порт volska из реестра не прочитан launcher'ом: %s" % before)

    (root / REGISTRY_REL).write_text(
        _registry_yaml([("volska", True, MOVED_PORT),
                        ("yarina", True, YARINA_PORT),
                        ("demo", False, None)]), encoding="utf-8")
    after = _launcher_ports(mod, "volska", root)
    assert any(after.get(n) == MOVED_PORT for n in names), (
        "порт volska в реестре сменился на %d, launcher отдаёт %s — значит он "
        "читает не реестр, а собственное число" % (MOVED_PORT, after))


def test_a_slug_without_a_panel_section_falls_back_and_does_not_borrow(launcher_on):
    """Слаг БЕЗ секции: умолчание, а НЕ чужой порт.

    §3 оставляет `DEFAULT_PORT` умолчанием ровно для этого случая. Отдать
    здесь порт соседа значило бы поднять второй инстанс поверх первого.
    """
    mod, root = launcher_on
    (root / REGISTRY_REL).write_text(
        _registry_yaml([("volska", True, None),
                        ("yarina", True, YARINA_PORT)]), encoding="utf-8")
    found = _launcher_ports(mod, "volska", root)
    borrowed = {n: v for n, v in found.items() if v == YARINA_PORT}
    assert not borrowed, (
        "для слага без секции `panel` launcher отдал ПОРТ СОСЕДА (%d): %s — "
        "это второй инстанс поверх первого" % (YARINA_PORT, borrowed))


# ── §5.3: канал, по которому порт узнаёт PowerShell-гардиан ────────────────
def test_the_json_plan_carries_the_panel_port_for_powershell(tmp_path):
    """Гардиан — .ps1, YAML он не читает.

    Единственный канал реестра наружу, который в этом дереве уже есть, —
    JSON-план `chatter.registry_cli.build_plan` («наружу отдаётся готовый
    JSON-план, PowerShell остаётся тонким исполнителем», докстринг
    `chatter/core/client_registry.py`). Порт обязан ехать этим каналом, иначе
    гардиан заведёт СВОЁ число — четвёртое.

    Ключ в плане не пинится по имени: ищем значение порта где угодно в записи
    клиента. Имя — дело автора кода, наличие — дело спеки.
    """
    from chatter.registry_cli import build_plan

    text = _registry_yaml(TWO_PANELS)
    plan = build_plan(text, root=str(tmp_path),
                      session_available=lambda s: True,
                      client_dir_exists=lambda s: True)
    assert not plan.get("fatal"), plan
    by_slug = {c.get("slug"): c for c in plan.get("clients") or []}
    assert "volska" in by_slug, plan

    def _values(obj):
        if isinstance(obj, dict):
            for v in obj.values():
                yield from _values(v)
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                yield from _values(v)
        else:
            yield obj

    assert VOLSKA_PORT in list(_values(by_slug["volska"])), (
        "в JSON-плане для PowerShell нет порта volska (%d) — гардиану неоткуда "
        "узнать порт слага, и он заведёт своё число: %s"
        % (VOLSKA_PORT, by_slug["volska"]))
    assert YARINA_PORT in list(_values(by_slug["yarina"])), (
        "в JSON-плане нет порта yarina (%d): %s" % (YARINA_PORT, by_slug["yarina"]))
