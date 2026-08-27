# -*- coding: utf-8 -*-
"""С11: порт клиентской панели — ОДНО ЧИСЛО, объявленное В ОДНОМ МЕСТЕ.

ПОЧЕМУ ЭТОТ СТОРОЖ ВООБЩЕ СУЩЕСТВУЕТ — не изменилось, и это главное в файле.
Расхождение числа порта не даёт ошибки ни в одном месте по отдельности. Оно
даёт ровно ту картину, ради которой всё это писалось: гардиан считает панель
мёртвой (стучится не туда) и каждые 15 секунд поднимает ЖИВУЮ поверх живой, а
проба при этом зелёная. Или наоборот — проба вечно красная на здоровой панели,
и её перестают читать.

ЧТО ИЗМЕНИЛОСЬ: ИНВАРИАНТ, А НЕ ПРИЧИНА.

Первая редакция (спека `2026-08-20-client-panel-supervisor.md`, §3.1) стерегла
«три места держат ОДНО число»: `run_panel_client.DEFAULT_PORT`, `-Port`
гардиана и `PANEL_CLIENT_PORT` в watchdog. Три места — это было признание
поражения: общей константы у python с PowerShell быть не может, и всё, что
оставалось, — требовать их равенства.

Арка `2026-08-27-volska-panel-instance` (§3) убрала два места из трёх. Порт
слага живёт в `chatter/clients/registry.yaml`, секцией `panel: {port: N}`, —
это единственное место, которое УЖЕ читают обе стороны: watchdog берёт оттуда
состав фермы, PowerShell-гардиан — desired state через `chatter.registry_cli`.
Поэтому инвариант стал сильнее и проще:

    порт слага объявлен РОВНО В ОДНОМ месте — в реестре,
    а код литерала порта НЕ ДЕРЖИТ.

ЕДИНСТВЕННОЕ ИСКЛЮЧЕНИЕ, И ОНО ОСОЗНАННОЕ: `DEFAULT_PORT` в
`run_panel_client.py` остаётся умолчанием ПОСЛЕДНЕЙ НАДЕЖДЫ — для слага, у
которого секции `panel` нет вовсе. Поднять панель руками на стенде — законное
действие, им пользуется сама приёмка арки (§7), и запрещать его значило бы
запретить то, чем арка проверяется. Умолчание живёт под ИМЕНЕМ и в
единственном экземпляре на файл: голый литерал в выражении сверке недоступен,
а значит разъедется молча.

ЧЕТЫРЕ СВОЙСТВА, КОТОРЫЕ ЗДЕСЬ УДЕРЖИВАЮТСЯ:

1. сверка СТАТИЧЕСКАЯ и по ЛИТЕРАЛАМ. Импортировать и сравнивать значения
   по-прежнему нельзя, и по той же причине: PowerShell из pytest не
   импортируется, а сравнение двух питоновских констант, одна из которых
   присвоена из другой, согласно ПО ОПРЕДЕЛЕНИЮ — и промолчит про место,
   которое и разъедется;
2. список мест — ЛИТЕРАЛЬНЫЙ и в ОБЕ стороны ([[jarvis-literal-lists-not-
   introspection]]): и «здесь число быть обязано», и «нигде больше его быть не
   должно» — счётом по дереву, а не перебором знакомых имён;
3. СЛАГИ НЕ ПЕРЕЧИСЛЯЮТСЯ. Набор портов берётся ИЗ РЕЕСТРА, поэтому третий
   слаг с панелью не требует правки этого файла. Сторож, который пришлось бы
   дописывать на каждого нового клиента, стал бы перечислением — и промолчал
   бы ровно про того клиента, которого забыли дописать;
4. PowerShell-сторона числа не носит вовсе: `-Port 0` означает «спроси
   реестр», а отсутствие порта — ОТКАЗ поднимать панель, а не умолчание.
   Умолчание-число на этой стороне пережило бы правку реестра.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY = REPO_ROOT / "chatter" / "clients" / "registry.yaml"
LAUNCHER = REPO_ROOT / "scripts" / "run_panel_client.py"
GUARDIAN = REPO_ROOT / "scripts" / "panel_client_guardian_detached.ps1"
REGISTRAR = REPO_ROOT / "scripts" / "register_panel_client_guardian.ps1"
WATCHDOG = REPO_ROOT / "scripts" / "ops_watchdog.py"

REGISTRY_REL = "chatter/clients/registry.yaml"

# ── ЛИТЕРАЛЬНЫЙ СПИСОК МЕСТ, которым позволено держать УМОЛЧАНИЕ ───────────
#
# Умолчание — это НЕ порт слага. Оно применяется только там, где секции
# `panel` нет вовсе, и обязано жить под именем: голый литерал в выражении
# сверке недоступен.
#
#   required — обязано существовать: без него панель нельзя поднять руками;
#   permitted — если константа ещё есть, литерал в этом файле законен.
REQUIRED_FALLBACKS = {
    "scripts/run_panel_client.py": "DEFAULT_PORT",
}
PERMITTED_FALLBACKS = {
    "scripts/run_panel_client.py": "DEFAULT_PORT",
    # Умолчание пробы для снимка без порта. Спека §3 оставляет его в том же
    # качестве; если автор кода его убрал — сторож не краснеет, но и лишнего
    # литерала не пропустит.
    "scripts/ops_watchdog.py": "PANEL_CLIENT_PORT",
}
# Места, которым литерал порта не позволен НИКАКОЙ. Обе — PowerShell: они
# спрашивают порт у реестра через `chatter.registry_cli`.
NO_LITERAL_AT_ALL = {
    "scripts/panel_client_guardian_detached.ps1": "гардиан клиентской панели",
    "scripts/register_panel_client_guardian.ps1": "регистратор задачи",
}
# Порты, занятые НЕ панелями клиентов (§2 спеки). Панель, севшая сюда,
# отберёт порт у бэкенда или у стенда приёмки.
TAKEN_BY_OTHERS = {8010: "бэкенд Джарвиса", 8013: "стенд приёмки"}

# ── ЛИТЕРАЛЬНЫЙ ЯКОРЬ §6.4, ВТОРАЯ СТОРОНА ПИНА ───────────────────────────
#
# §5.1 называет оба слага и оба числа поимённо: volska — 8012, yarina — 8011.
# Здесь они повторены ЛИТЕРАЛОМ, и это не дубль реестра, а единственное, что
# держит всю сверку от вырождения: набор портов ниже читается ИЗ РЕЕСТРА, и
# при пустом наборе секций обе половины «нигде больше» становятся ноль=ноль —
# то есть сторож зеленеет ровно в тот день, когда арку откатили наполовину
# ([[jarvis-literal-lists-not-introspection]]).
#
# Пин — МИНИМУМ, а не равенство: третий слаг с панелью правки этого файла не
# требует (свойство 3 выше), он проверится выведенным набором. Но эти два
# обязаны быть объявлены именно этими числами, и тихая перенумерация — это
# решение владельца, которое обязано быть громким.
DECLARED_PANEL_PORTS = {"volska": 8012, "yarina": 8011}

# Корни счёта — литеральные. `tests/` и `docs/` вне счёта намеренно: сторожа и
# спеки называют числа по должности, и запрет на это сделал бы невозможным
# разговор о порте вообще.
SCANNED_ROOTS = ("scripts", "chatter", "app")


# ── чтение источников ──────────────────────────────────────────────────────
def _panel_ports_of_text(text: str) -> dict:
    """`{slug: port}` из ТЕКСТА реестра.

    Отдельной функцией — чтобы то же самое можно было проверить на
    синтетическом реестре с ТРЕТЬИМ слагом и убедиться, что сторож не
    перечисляет клиентов поимённо.
    """
    data = yaml.safe_load(text) or {}
    clients = data.get("clients") or {}
    out = {}
    for slug, cfg in clients.items():
        if not isinstance(cfg, dict):
            continue
        panel = cfg.get("panel")
        if panel is None:
            continue
        assert isinstance(panel, dict), (
            "секция `panel` слага %s не словарь (%r): контракт — "
            "`panel: {port: N}`" % (slug, panel))
        out[str(slug)] = panel.get("port")
    return out


def _panel_ports() -> dict:
    assert REGISTRY.exists(), "нет реестра %s — порту негде быть объявленным" % REGISTRY
    return _panel_ports_of_text(REGISTRY.read_text(encoding="utf-8"))


_BLOCK_COMMENT = re.compile(r"<#.*?#>", re.S)


def _ps_code(text: str) -> str:
    """Скрипт без комментариев — иначе номер порта, названный в шапке для
    человека, читался бы как место объявления."""
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


def _py_int_literals(path: Path) -> list:
    """Целые ЛИТЕРАЛЫ файла.

    Строки и докстринги сюда не попадают намеренно: мутационные гейты держат
    номера портов ТЕКСТОМ (`scripts/mutate_panel_client_supervisor.py`), и это
    не место объявления, а мишень.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError, ValueError):
        return []
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, int)
            and not isinstance(n.value, bool)]


def _py_module_constants(path: Path) -> dict:
    """`{имя: значение}` модульных int-констант — то, что человек напечатал."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError, ValueError):
        return {}
    out = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Constant):
            continue
        value = node.value.value
        if not isinstance(value, int) or isinstance(value, bool):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                out[target.id] = value
    return out


def _ps_int_literals(path: Path) -> list:
    try:
        code = _ps_code(path.read_text(encoding="utf-8-sig", errors="replace"))
    except OSError:
        return []
    return [int(m) for m in re.findall(r"(?<![\w.])(\d{4,5})(?![\w.])", code)]


def _yaml_ints(path: Path) -> list:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return []

    def walk(obj):
        if isinstance(obj, dict):
            for v in obj.values():
                yield from walk(v)
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                yield from walk(v)
        elif isinstance(obj, int) and not isinstance(obj, bool):
            yield obj

    return list(walk(data))


def _scanned_files():
    for root in SCANNED_ROOTS:
        base = REPO_ROOT / root
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.suffix.lower() in (".py", ".ps1", ".yaml", ".yml"):
                yield path
    for path in REPO_ROOT.glob("*.ps1"):
        yield path


def _declarations_of(port: int) -> dict:
    """`{относительный путь: сколько раз}` — где число объявлено ЗНАЧЕНИЕМ."""
    out = {}
    for path in _scanned_files():
        suffix = path.suffix.lower()
        if suffix == ".py":
            values = _py_int_literals(path)
        elif suffix == ".ps1":
            values = _ps_int_literals(path)
        else:
            values = _yaml_ints(path)
        hits = sum(1 for v in values if v == port)
        if hits:
            out[path.relative_to(REPO_ROOT).as_posix()] = hits
    return out


# ── 1. Реестр — то самое ЕДИНСТВЕННОЕ место ────────────────────────────────
def test_the_registry_declares_a_port_for_every_panel_slug():
    ports = _panel_ports()
    assert ports, (
        "в реестре нет НИ ОДНОЙ секции `panel` — порт слага не объявлен нигде, "
        "и всё, что поднимает панели, живёт умолчаниями")
    for slug, port in ports.items():
        assert isinstance(port, int) and not isinstance(port, bool), (
            "порт слага %s объявлен не целым числом (%r): строка «8012» "
            "доедет до `--port` и до сравнения по-разному" % (slug, port))
        assert 1024 < port < 65536, (
            "порт слага %s вне диапазона: %r" % (slug, port))


@pytest.mark.parametrize("slug,port", sorted(DECLARED_PANEL_PORTS.items()))
def test_the_registry_declares_the_two_ports_the_spec_named_out_loud(slug, port):
    """§6.4, литеральная половина: «здесь число быть ОБЯЗАНО».

    Без неё вся вторая половина («нигде больше») вырождается: она перебирает
    порты, ВЫВЕДЕННЫЕ из того же реестра, и на реестре без секций проходит по
    пустому множеству. Ноль расхождений на нуле портов — это зелёное по
    построению, и приходит оно ровно тогда, когда секцию потеряли.
    """
    ports = _panel_ports()
    assert ports.get(slug) == port, (
        "реестр объявляет слагу %s порт %r, а §5.1 спеки называет %d. Либо "
        "секция потеряна (и тогда сверка «нигде больше» идёт по пустому "
        "множеству), либо число сменили молча" % (slug, ports.get(slug), port))


def test_two_slugs_never_share_a_port_in_the_registry():
    """Здесь — только литеральная сверка объявлений.

    Что валидация реестра ОТКЛОНЯЕТ такой реестр, проверяется поведением в
    `tests/test_volska_panel_port_conflict.py`; здесь пин на то, что боевой
    файл до этого не доводит.
    """
    ports = _panel_ports()
    seen = {}
    for slug, port in ports.items():
        seen.setdefault(port, []).append(slug)
    shared = {p: s for p, s in seen.items() if len(s) > 1}
    assert not shared, (
        "слаги делят порт панели: %s. Два гардиана начнут сносить панели друг "
        "друга, и обе пробы увидят живой /health — просто не тот" % shared)


@pytest.mark.parametrize("port,who", sorted(TAKEN_BY_OTHERS.items()))
def test_no_panel_sits_on_a_port_that_belongs_to_someone_else(port, who):
    squatters = sorted(s for s, p in _panel_ports().items() if p == port)
    assert not squatters, (
        "слаг(и) %s объявлены на порту %d, который занимает %s (§2 спеки)"
        % (squatters, port, who))


def test_the_registry_declares_each_port_exactly_once():
    ports = _panel_ports()
    for slug, port in ports.items():
        hits = _declarations_of(port).get(REGISTRY_REL, 0)
        assert hits == 1, (
            "число %d (слаг %s) встречается в реестре %d раз(а): одна запись — "
            "одно число" % (port, slug, hits))


# ── 2. Сердцевина: код литерала порта НЕ ДЕРЖИТ ────────────────────────────
def test_no_panel_port_is_declared_anywhere_outside_the_registry_and_the_fallbacks():
    """🔴 СЕРДЦЕВИНА нового инварианта, и она НЕ ПЕРЕЧИСЛЯЕТ СЛАГОВ.

    Набор портов берётся из реестра, поэтому третий слаг с панелью проверится
    сам, без правки этого файла.
    """
    ports = _panel_ports()
    complaints = []
    for slug, port in sorted(ports.items()):
        places = _declarations_of(port)
        assert REGISTRY_REL in places, (
            "порт слага %s (%d) не объявлен в самом реестре — счёт мест "
            "потерял источник правды: %s" % (slug, port, places))
        for rel, hits in sorted(places.items()):
            if rel == REGISTRY_REL:
                continue
            if rel in PERMITTED_FALLBACKS:
                continue
            complaints.append("%s: порт %d слага %s объявлен %d раз(а)"
                              % (rel, port, slug, hits))
    assert not complaints, (
        "порт слага объявлен ЗНАЧЕНИЕМ вне реестра:\n  %s\n"
        "Это второй источник правды об адресе. Он не даёт ошибки сегодня — он "
        "даёт «гардиан поднимает живую панель поверх живой, а проба зелёная» в "
        "день, когда порт поменяют в одном месте из двух."
        % "\n  ".join(complaints))


@pytest.mark.parametrize("rel,who", sorted(NO_LITERAL_AT_ALL.items()))
def test_the_powershell_side_carries_no_port_literal_at_all(rel, who):
    """PowerShell спрашивает порт у реестра и своего числа не имеет.

    Обратная сторона того же пина: если бы литерал остался, он пережил бы
    правку и реестра, и задачи.
    """
    path = REPO_ROOT / rel
    assert path.exists(), "нет скрипта %s" % rel
    known = set(_panel_ports().values()) | set(TAKEN_BY_OTHERS)
    for name, const_file in ((n, REPO_ROOT / f) for f, n in PERMITTED_FALLBACKS.items()):
        known |= {v for k, v in _py_module_constants(const_file).items() if k == name}
    hits = sorted({n for n in _ps_int_literals(path) if n in known})
    assert not hits, (
        "%s держит номера портов в КОДЕ: %s. Порт слага живёт в реестре, и "
        "второе его написание разъедется в день смены порта" % (who, hits))


def test_the_guardian_port_parameter_is_a_sentinel_not_a_port():
    """`-Port 0` = «спроси реестр». Реальный номер в умолчании — это второй
    источник правды, причём с приоритетом над реестром."""
    assert GUARDIAN.exists(), "нет скрипта %s" % GUARDIAN
    code = _ps_code(GUARDIAN.read_text(encoding="utf-8-sig"))
    for value in re.findall(r"\$Port\s*=\s*(-?\d+)", code):
        assert int(value) <= 0, (
            "у гардиана `$Port` имеет умолчанием реальный порт %s — реестр "
            "перестал быть источником правды для стороны, которая ПОДНИМАЕТ "
            "панель" % value)


def test_the_guardian_reaches_for_the_registry():
    """След обращения к реестру обязан быть.

    Конкретный вызов не пинится: это дело автора кода. Пинится то, что порт не
    берётся ниоткуда — PowerShell YAML не читает, и единственный мост к
    реестру в этом дереве один.
    """
    assert GUARDIAN.exists(), "нет скрипта %s" % GUARDIAN
    code = _ps_code(GUARDIAN.read_text(encoding="utf-8-sig")).lower()
    marks = [m for m in ("registry", "реестр", "registry_cli", "registry.yaml")
             if m in code]
    assert marks, (
        "в коде гардиана нет ни одного обращения к реестру: порт слага "
        "берётся откуда-то ещё, и это место переживёт правку registry.yaml")


# ── 3. Умолчание последней надежды: под именем и в единственном числе ──────
@pytest.mark.parametrize("rel,const", sorted(REQUIRED_FALLBACKS.items()))
def test_the_last_hope_default_still_exists_under_its_name(rel, const):
    """Поднять панель руками на стенде — законное действие (§7 приёмки).

    Исчезнувшее умолчание запретило бы то, чем арка проверяется; переехавшее
    под другое имя протушило бы список мест МОЛЧА.
    """
    path = REPO_ROOT / rel
    assert path.exists(), "нет файла %s" % rel
    constants = _py_module_constants(path)
    assert const in constants, (
        "в %s больше нет модульной константы %s: умолчание последней надежды "
        "исчезло, а литеральный список мест этого сторожа протух молча. "
        "Модульные константы файла: %s" % (rel, const, sorted(constants)))


@pytest.mark.parametrize("rel,const", sorted(PERMITTED_FALLBACKS.items()))
def test_a_fallback_file_names_its_number_exactly_once(rel, const):
    """Одно место на файл.

    Даже при верном умолчании второе такое же число, вписанное строкой ниже,
    переживёт правку константы — и разъедется молча.
    """
    path = REPO_ROOT / rel
    if not path.exists():
        pytest.skip("файла %s нет" % rel)
    constants = _py_module_constants(path)
    if const not in constants:
        pytest.skip("константы %s в %s больше нет — проверяет соседний сторож"
                    % (const, rel))
    value = constants[const]
    hits = sum(1 for v in _py_int_literals(path) if v == value)
    assert hits == 1, (
        "в %s число %d встречается %d раз(а): внутри одного файла уже два "
        "числа на одну вещь" % (rel, value, hits))


def test_all_remaining_fallbacks_carry_ONE_AND_THE_SAME_number():
    """Наследник прежней сердцевины С11.

    Мест стало меньше, но пока их больше одного, они обязаны быть равны ДРУГ
    ДРУГУ — не «каждое равно 8011». Константу когда-нибудь поменяют, и важно
    именно взаимное согласие.
    """
    values = {}
    for rel, const in PERMITTED_FALLBACKS.items():
        constants = _py_module_constants(REPO_ROOT / rel)
        if const in constants:
            values["%s.%s" % (rel, const)] = constants[const]
    assert values, (
        "умолчаний не осталось вовсе: поднять панель руками на стенде стало "
        "нечем")
    assert len(set(values.values())) == 1, (
        "умолчания порта разъехались: %s. Меньшее из двух чисел гасит большее "
        "МОЛЧА" % values)


def test_the_fallback_is_a_port_the_registry_also_knows():
    """Умолчание не имеет права быть числом ниоткуда.

    Умолчание, не совпадающее ни с одним объявленным портом, означает, что
    ручной запуск на стенде поднимет панель туда, куда не смотрит ни гардиан,
    ни проба, — и выглядеть это будет как здоровый старт.
    """
    ports = set(_panel_ports().values())
    constants = _py_module_constants(LAUNCHER)
    const = REQUIRED_FALLBACKS["scripts/run_panel_client.py"]
    assert const in constants, "нет %s в %s" % (const, LAUNCHER)
    assert constants[const] in ports, (
        "умолчание %s = %d не совпадает ни с одним портом, объявленным в "
        "реестре (%s): ручной запуск уедет туда, куда никто не смотрит"
        % (const, constants[const], sorted(ports)))


# ── 3b. САМ СКАНЕР: он обязан УМЕТЬ находить ───────────────────────────────
#
# Вся вторая половина §6.4 держится на том, что счётчик объявлений что-то
# видит. Сканер, вернувший пустой список, делает «нигде больше» зелёным по
# построению — и делает это молча, при любом состоянии кода. Поэтому у каждого
# сканера здесь стоит ПОЛОЖИТЕЛЬНЫЙ КОНТРОЛЬ на заведомо известном входе.
def test_the_python_scanner_sees_a_literal_and_ignores_the_docstring(tmp_path):
    """Число в коде — объявление; число в тексте — разговор о числе."""
    path = tmp_path / "probe.py"
    path.write_text('"""Порт 8099 назван в докстринге."""\nPORT = 8123\n'
                    'OTHER = "8177"\n', encoding="utf-8")
    got = _py_int_literals(path)
    assert 8123 in got, (
        "сканер питоновских литералов не видит присвоенного числа — счёт мест "
        "по построению пуст: %s" % got)
    assert 8099 not in got, (
        "число из докстринга засчитано объявлением: %s" % got)
    assert 8177 not in got, (
        "число из СТРОКИ засчитано объявлением: мутационные гейты держат порты "
        "текстом, и это мишень, а не место объявления: %s" % got)
    assert _py_module_constants(path).get("PORT") == 8123, (
        "разбор модульных констант не видит присвоения — литеральный список "
        "мест сверять нечем")


def test_the_powershell_scanner_sees_code_and_ignores_comments(tmp_path):
    """PowerShell-сторона пинится ТЕМ ЖЕ сканером, и он не должен быть слеп.

    Кавычки НЕ спасают: `-Port '8013'` — такое же объявление, как и без них,
    поэтому число в строке засчитывается. А число в комментарии — нет: шапка
    скрипта имеет право называть порт для человека.
    """
    path = tmp_path / "probe.ps1"
    path.write_text("param([int]$Port = 8123)\n"
                    "# в комментарии 8099\n"
                    "<# в блоке 8177 #>\n"
                    "$args = @('-Port', '8013')\n", encoding="utf-8-sig")
    got = _ps_int_literals(path)
    assert 8123 in got, (
        "сканер PowerShell не видит числа в коде: пин «PowerShell не носит "
        "номера портов» стал бы зелёным по построению: %s" % got)
    assert 8013 in got, (
        "число в кавычках не засчитано: `-Port '8013'` — такое же второе "
        "написание порта: %s" % got)
    assert 8099 not in got and 8177 not in got, (
        "число из комментария засчитано объявлением: %s" % got)


def test_the_declaration_count_actually_finds_a_known_literal():
    """Положительный контроль на БОЕВОМ дереве, а не на синтетике.

    `DEFAULT_PORT` в `run_panel_client.py` — заведомо существующее объявление
    (его существование пинит `test_the_last_hope_default_still_exists_under_its
    _name`). Если обход дерева его не находит, значит не находит НИЧЕГО, и
    сердцевина «порт объявлен только в реестре» проходит вхолостую.
    """
    constants = _py_module_constants(LAUNCHER)
    const = REQUIRED_FALLBACKS["scripts/run_panel_client.py"]
    assert const in constants, "нет %s в %s — проверяет соседний сторож" % (const, LAUNCHER)
    places = _declarations_of(constants[const])
    assert "scripts/run_panel_client.py" in places, (
        "обход дерева не нашёл заведомо существующее объявление %s = %d. "
        "Счётчик мест слеп, и весь пин «нигде больше» — зелёный по построению. "
        "Найдено: %s" % (const, constants[const], sorted(places)))


# ── 4. Сторож не перечисляет клиентов ──────────────────────────────────────
def test_a_third_panel_slug_needs_no_edit_of_this_guard():
    """Требование §6.4, проверенное на самом сторо́же.

    Набор портов читается из реестра одной функцией; ей подсовывается
    СИНТЕТИЧЕСКИЙ реестр с третьим слагом. Если сторож где-то перечисляет
    клиентов поимённо, третий слаг из него не выйдет — и сторож промолчит
    ровно про того клиента, которого забыли дописать.
    """
    text = "\n".join([
        "clients:",
        "  volska:", "    enabled: true", "    panel:", "      port: 8012",
        "  yarina:", "    enabled: true", "    panel:", "      port: 8011",
        "  tretiy:", "    enabled: true", "    panel:", "      port: 8014",
        "  bez_paneli:", "    enabled: true",
    ]) + "\n"
    ports = _panel_ports_of_text(text)
    assert ports == {"volska": 8012, "yarina": 8011, "tretiy": 8014}, (
        "разбор реестра не обобщается на третий слаг: %s" % ports)
    assert "bez_paneli" not in ports, (
        "слаг без секции `panel` попал в набор портов: %s" % ports)
