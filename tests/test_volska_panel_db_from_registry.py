# -*- coding: utf-8 -*-
"""§6.8 и §6.9 спеки `2026-08-27-volska-panel-instance` (ПОПРАВКА 1, §3b):
БАЗА ИНСТАНСА БЕРЁТСЯ ИЗ РЕЕСТРА, А НЕ ВЫВОДИТСЯ ИЗ СЛАГА; слаг без `db:` —
ОТКАЗ поднимать, а не умолчание.

Сторожа писаны ОТ ТЕКСТА СПЕКИ ([[jarvis-guards-not-by-the-plan-author]]).

🔴 ГЛАВНОЕ ПРО ПИН: ОН СТОИТ НА РАСХОЖДЕНИИ, А НЕ НА СОВПАДЕНИИ.

`build_instance_env` собирает базу строкой `.secrets/<slug>.db`. У `yarina`
выведенное имя СОВПАДАЕТ с объявленным в реестре (`.secrets/yarina.db`), и
поэтому сторож, проверяющий yarina, зелен ПО ПОСТРОЕНИЮ: он одинаково зелен и
при чтении реестра, и при выводе из слага, то есть не отличает исправленный код
от сегодняшнего. Расхождение живёт у `volska`: слаг `volska`, а в реестре
`db: .secrets/demo.db` («volska живёт на сессии demo-аккаунта» — пин в самом
реестре). Файла `.secrets/volska.db` не существует вовсе.

Поэтому КАЖДЫЙ сторож прямой половины стоит на слаге, у которого слаг и база
РАЗОШЛИСЬ, а совпадающий слаг присутствует только встречной половиной — как
доказательство, что реализация не сломала законный случай.

ЧЕМ ЭТО КОНЧИЛОСЬ БЫ — ЗЕЛЁНЫМ. Панель поднялась бы, порт занялся, `/health`
ответил, обе лампы (`escalation:volska`, `outgoing:volska`) погасли — а Ольга
увидела бы ПУСТУЮ ленту. Лампы гаснут НЕ ПО ДЕЛУ, то есть ровно тот класс, ради
которого арка затевалась, только наизнанку. Приёмка §7 п.2 это не ловит: ручка
отвечает, встречная проверка проходит, лента пустая
([[jarvis-panel-db-source-is-the-live-runner]],
[[jarvis-loud-failure-next-to-a-soothing-lamp]]).

ЧТО ЗДЕСЬ НЕ ПРОВЕРЯЕТСЯ ТЕКСТОМ И ПОЧЕМУ. Статический пин «в launcher'е нет
шаблона `%s.db`» был бы слепым к главному пути, которым дефект и приезжает:
`chatter.core.client_registry.parse_registry` САМА подставляет
`.secrets/<slug>.db`, когда `db:` в записи нет (строка 97, и это объявленный
контракт реестра: «минимальная запись — две строки, пути выводятся из slug'а»).
Реализация, взявшая базу «из реестра» через этот разбор, прошла бы текстовый
пин и привезла бы то же самое умолчание. Поэтому вся прямая половина —
ПОВЕДЕНЧЕСКАЯ, а §6.9 проверяется на реестре, где `db:` отсутствует ФИЗИЧЕСКИ.

ФОРМА ОТКАЗА НЕ ПИНИТСЯ. Спека называет исход («ОТКАЗ поднимать»), а не способ.
Отказом считается и исключение, и пустой `TAMAPI_DB` — потому что пустой
`TAMAPI_DB` уже отклоняется существующей `instance_env_problems`
(`REQUIRED_INSTANCE_VARS`), то есть доезжает до печатного «ОТКАЗ, инстанс не
поднят». Не отказом считается ровно одно: выданный `.secrets/<slug>.db`.
"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER_PATH = REPO_ROOT / "scripts" / "run_panel_client.py"
REGISTRY = REPO_ROOT / "chatter" / "clients" / "registry.yaml"
REGISTRY_REL = "chatter/clients/registry.yaml"

# ── СЛАГИ И БАЗЫ СТОРОЖА ───────────────────────────────────────────────────
# Слаг, у которого слаг и база РАЗОШЛИСЬ. Ровно на нём стоит прямая половина.
DIVERGING_SLUG = "volska"
DIVERGING_DB = ".secrets/demo.db"
# Слаг, у которого они СОВПАДАЮТ. Только встречная половина: сторож на нём
# зелен по построению и один ничего не доказывает.
COINCIDING_SLUG = "yarina"
COINCIDING_DB = ".secrets/yarina.db"
# Имя, которого нет ни в коде, ни в боевом реестре. Совпадение чисел и имён
# сегодня доказывает ноль; доказывает ДВИЖЕНИЕ за правкой одной строки YAML.
MOVED_DB = ".secrets/olga_2026_archive.db"

PORT_A = 8123
PORT_B = 8099

# Клиент реестра: (slug, enabled, db|None, port|None). `db=None` означает, что
# строки `db:` в записи НЕТ ВОВСЕ, — это и есть предмет §6.9.
_MISSING = object()


def _registry_yaml(clients) -> str:
    """`[(slug, enabled, db|None|_MISSING, port|None), ...]` -> текст реестра.

    Собираем ТЕКСТ, а не готовый словарь: предмет проверки — путь «строчка в
    YAML -> окружение инстанса» целиком. Словарь из моих рук проверял бы
    только то, что его скопировали.
    """
    lines = ["clients:"]
    for slug, enabled, db, port in clients:
        lines.append("  %s:" % slug)
        lines.append("    enabled: %s" % ("true" if enabled else "false"))
        lines.append("    personas: [%s]" % slug)
        lines.append("    session: .secrets/%s.session" % slug)
        if db is _MISSING:
            pass  # строки `db:` нет — §6.9
        elif db is None:
            lines.append('    db: ""')  # значение пустое — тоже не путь
        else:
            lines.append("    db: %s" % db)
        if port is not None:
            lines.append("    panel:")
            lines.append("      port: %d" % port)
    return "\n".join(lines) + "\n"


def _root_with(tmp_path: Path, clients) -> Path:
    """Временный корень с реестром и с ФАЙЛАМИ объявленных баз.

    Объявленные базы кладутся на диск НАСТОЯЩИМИ файлами, а выведенные из слага
    — нет. Это даёт сторожам вторую, независимую улику: «отданная база
    существует» отличает `demo.db` от `volska.db` даже там, где имя сравнивать
    было бы неудобно, и повторяет ровно ту разницу, что живёт в `.secrets`
    боевого дерева (там есть `demo.db` и `yarina.db`, и нет `volska.db`).
    """
    root = tmp_path / "repo"
    (root / "chatter" / "clients").mkdir(parents=True, exist_ok=True)
    (root / ".secrets").mkdir(parents=True, exist_ok=True)
    (root / REGISTRY_REL).write_text(_registry_yaml(clients), encoding="utf-8")
    for _slug, _enabled, db, _port in clients:
        if isinstance(db, str) and db:
            path = root / db
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"")
    return root


# Ферма прямой половины: расходящийся слаг и совпадающий рядом.
FARM = [(DIVERGING_SLUG, True, DIVERGING_DB, PORT_A),
        (COINCIDING_SLUG, True, COINCIDING_DB, PORT_B)]


def _launcher():
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import run_panel_client as rpc
    return rpc


def _call_build_env(mod, slug, root, monkeypatch):
    """Позвать `build_instance_env` на ВРЕМЕННОМ корне.

    Имя функции пинится, и это законно: §3b цитирует её строку дословно, то
    есть спека уже назвала место правки. Корень подставляется двумя способами
    сразу — аргументом и подменой модульного `_ROOT`: значение по умолчанию
    связывается на `def`, поэтому одной подмены атрибута мало, а одного
    аргумента мало, если автор кода перенёс чтение реестра в модульный
    помощник.
    """
    for attr in ("_ROOT", "ROOT"):
        if hasattr(mod, attr):
            monkeypatch.setattr(mod, attr, root)
    fn = getattr(mod, "build_instance_env", None)
    assert fn is not None, (
        "в scripts/run_panel_client.py нет `build_instance_env` — функции, "
        "чью строку `TAMAPI_DB` цитирует §3b. Если сборка окружения переехала, "
        "переехал и предмет этих сторожей, и молчать об этом нельзя")
    kwargs = {}
    params = inspect.signature(fn).parameters
    if "environ" in params:
        # Пустое окружение процесса намеренно: ключ инстанса здесь не предмет,
        # а живой `os.environ` сделал бы сторожа зависимым от машины.
        kwargs["environ"] = {}
    if "root" in params:
        kwargs["root"] = root
    try:
        return ("returned", fn(slug, **kwargs))
    except Exception as exc:  # noqa: BLE001 — форма отказа не пинится
        return ("raised", exc)


def _instance_db(mod, slug, root, monkeypatch):
    """-> `("ok", путь)` либо `("refused", чем именно отказано)`."""
    kind, payload = _call_build_env(mod, slug, root, monkeypatch)
    if kind == "raised":
        return "refused", "исключение %r" % (payload,)
    if not isinstance(payload, dict):
        return "refused", "окружение не словарь: %r" % (payload,)
    value = payload.get("TAMAPI_DB")
    if value is None or not str(value).strip():
        return "refused", "TAMAPI_DB пуст"
    return "ok", str(value)


def _env_of(mod, slug, root, monkeypatch) -> dict:
    kind, payload = _call_build_env(mod, slug, root, monkeypatch)
    assert kind == "returned" and isinstance(payload, dict), (
        "окружение инстанса %s не собралось вовсе: %r" % (slug, payload))
    return payload


def _slug_derived(root: Path, slug: str) -> Path:
    """Путь, который получается ВЫВОДОМ ИЗ СЛАГА, — то, чего быть не должно."""
    return root / ".secrets" / ("%s.db" % slug)


def _same_file(a, b) -> bool:
    return os.path.normcase(os.path.normpath(str(a))) == \
        os.path.normcase(os.path.normpath(str(b)))


# ══ §6.8 ПРЯМАЯ ПОЛОВИНА: база берётся ИЗ РЕЕСТРА ═════════════════════════
def test_the_db_of_a_diverging_slug_is_the_one_the_registry_declares(tmp_path, monkeypatch):
    """🔴 СЕРДЦЕВИНА §6.8, и она стоит НА РАСХОЖДЕНИИ.

    Реестр объявляет `volska: db: .secrets/demo.db`. Инстанс обязан получить
    именно её. Сегодняшний код отдаёт `.secrets/volska.db` — файла, которого
    нет вовсе, и панель показала бы ПУСТУЮ ленту при полностью зелёных лампах.
    """
    mod = _launcher()
    root = _root_with(tmp_path, FARM)
    kind, value = _instance_db(mod, DIVERGING_SLUG, root, monkeypatch)
    assert kind == "ok", (
        "инстанс %s с ОБЪЯВЛЕННОЙ базой %s не собрался: %s"
        % (DIVERGING_SLUG, DIVERGING_DB, value))
    assert _same_file(value, root / DIVERGING_DB), (
        "база инстанса %s = %r, а реестр объявляет %s. Слаг и база разошлись — "
        "и это единственное место, где видно, читается реестр или имя выводится "
        "из слага" % (DIVERGING_SLUG, value, DIVERGING_DB))


def test_the_db_of_a_diverging_slug_is_never_the_name_derived_from_the_slug(tmp_path, monkeypatch):
    """Та же истина, названная НАИЗНАНКУ и дословно по §3b.

    Отдельным сторожем, а не вторым `assert` в предыдущем: «отдали не то» и
    «отдали ровно выведенное из слага» — разные диагнозы, и первый может
    случиться при живом, но кривом чтении реестра.
    """
    mod = _launcher()
    root = _root_with(tmp_path, FARM)
    _kind, value = _instance_db(mod, DIVERGING_SLUG, root, monkeypatch)
    forbidden = _slug_derived(root, DIVERGING_SLUG)
    assert not _same_file(value, forbidden), (
        "база инстанса выведена ИЗ СЛАГА (%s). Такого файла не существует, и "
        "панель поднялась бы с пустой лентой: /health отвечает, лампы гаснут, "
        "Ольга видит пустой экран" % forbidden)


def test_the_declared_db_is_the_file_that_actually_exists(tmp_path, monkeypatch):
    """Вторая, независимая улика: отданная база СУЩЕСТВУЕТ.

    Во временном корне лежит `demo.db` и не лежит `volska.db` — ровно как в
    боевом `.secrets`. Сторож на имени и сторож на существовании ловят одно и
    то же двумя разными способами, и второй не зависит от того, как автор кода
    напишет путь (слэши, регистр, абсолютность).
    """
    mod = _launcher()
    root = _root_with(tmp_path, FARM)
    _kind, value = _instance_db(mod, DIVERGING_SLUG, root, monkeypatch)
    assert Path(str(value)).exists(), (
        "база инстанса %s указывает на несуществующий файл %r. В корне лежит "
        "объявленная реестром %s — значит отдано что-то другое"
        % (DIVERGING_SLUG, value, DIVERGING_DB))


def test_moving_the_db_in_the_registry_moves_the_instance(tmp_path, monkeypatch):
    """ДВИЖЕНИЕ за правкой одной строки YAML.

    Совпадение имён сегодня не доказывает ничего: реализация, выводящая имя из
    слага, совпала бы с реестром у yarina и разъехалась бы у volska. Здесь
    правится сам реестр, и база обязана поехать за ним — как порт в §6.1.
    """
    mod = _launcher()
    root = _root_with(tmp_path, FARM)
    _kind, before = _instance_db(mod, DIVERGING_SLUG, root, monkeypatch)
    assert _same_file(before, root / DIVERGING_DB), (
        "исходная база из реестра не прочитана: %r" % (before,))

    moved = [(DIVERGING_SLUG, True, MOVED_DB, PORT_A),
             (COINCIDING_SLUG, True, COINCIDING_DB, PORT_B)]
    (root / REGISTRY_REL).write_text(_registry_yaml(moved), encoding="utf-8")
    (root / MOVED_DB).write_bytes(b"")

    _kind2, after = _instance_db(mod, DIVERGING_SLUG, root, monkeypatch)
    assert _same_file(after, root / MOVED_DB), (
        "база volska в реестре сменилась на %s, а инстанс получает %r — у базы "
        "ДВА источника правды, и разъедутся они в день, когда клиента переселят"
        % (MOVED_DB, after))


def test_the_db_is_absolute_and_lives_inside_the_root(tmp_path, monkeypatch):
    """Реестр объявляет путь ОТНОСИТЕЛЬНЫЙ; процессу нужен абсолютный.

    Относительный путь, доехавший до `TAMAPI_DB`, разрешался бы от cwd
    процесса, а cwd у панели задаёт гардиан
    ([[jarvis-subprocess-cwd-is-code-tree]]). Промах читался бы как пустая
    лента — то есть неотличимо от дефекта §3b.
    """
    mod = _launcher()
    root = _root_with(tmp_path, FARM)
    _kind, value = _instance_db(mod, DIVERGING_SLUG, root, monkeypatch)
    path = Path(str(value))
    assert path.is_absolute(), (
        "база инстанса отдана относительным путём %r: она разрешится от cwd "
        "процесса, а его задаёт гардиан" % (value,))
    assert _same_file(path.parent, root / ".secrets"), (
        "база инстанса ведёт за пределы корня, из которого поднят инстанс: %r "
        "при корне %s" % (value, root))


def test_the_slug_of_the_instance_stays_the_slug(tmp_path, monkeypatch):
    """Граница правки: из реестра берётся БАЗА, а не ИМЯ КЛИЕНТА.

    `TAMAPI_SLUG` — то, что панель показывает в шапке. Съехав на имя владельца
    базы, инстанс volska назвался бы `demo`: та же переписка, но подписанная
    чужим именем, — и это выглядело бы как исправная панель.
    """
    mod = _launcher()
    root = _root_with(tmp_path, FARM)
    env = _env_of(mod, DIVERGING_SLUG, root, monkeypatch)
    assert env.get("TAMAPI_SLUG") == DIVERGING_SLUG, (
        "инстанс %s объявил себя %r: имя клиента поехало за именем базы"
        % (DIVERGING_SLUG, env.get("TAMAPI_SLUG")))


def test_the_heartbeat_stays_derived_from_the_slug(tmp_path, monkeypatch):
    """Встречная граница: НЕ ВСЁ выведенное из слага является дефектом.

    Отметка живости — `state/chatter_heartbeat_<slug>.txt`, и пишет её раннер
    ПО СЛАГУ. Перевод её «на реестр» заодно с базой сломал бы согласие с
    раннером — то есть починка §3b имеет право тронуть ровно одну строку.
    """
    mod = _launcher()
    root = _root_with(tmp_path, FARM)
    env = _env_of(mod, DIVERGING_SLUG, root, monkeypatch)
    beat = str(env.get("TAMAPI_HEARTBEAT") or "")
    assert DIVERGING_SLUG in os.path.basename(beat), (
        "отметка живости инстанса %s = %r: она обязана остаться выведенной из "
        "СЛАГА — её по слагу пишет раннер" % (DIVERGING_SLUG, beat))


# ══ §6.8 ВСТРЕЧНАЯ ПОЛОВИНА: совпадающий слаг не сломан ═══════════════════
def test_the_coinciding_slug_still_gets_its_own_db(tmp_path, monkeypatch):
    """🟡 ЗЕЛЁН ПО ПОСТРОЕНИЮ — и стоит здесь именно поэтому.

    У yarina выведенное из слага имя совпадает с объявленным, поэтому этот
    сторож одинаково зелен и до правки, и после: доказывать чтение реестра он
    не умеет. Его работа другая — не дать «починке» отобрать базу у клиента,
    чья панель работает прямо сейчас (§9: живую панель yarina не трогаем).
    """
    mod = _launcher()
    root = _root_with(tmp_path, FARM)
    kind, value = _instance_db(mod, COINCIDING_SLUG, root, monkeypatch)
    assert kind == "ok", (
        "инстанс %s, у которого слаг и база совпадают, перестал собираться: %s"
        % (COINCIDING_SLUG, value))
    assert _same_file(value, root / COINCIDING_DB), (
        "база живого клиента %s = %r вместо объявленной %s"
        % (COINCIDING_SLUG, value, COINCIDING_DB))


def test_two_slugs_of_one_registry_do_not_get_the_same_db(tmp_path, monkeypatch):
    """Реализация, отдающая всем ПЕРВУЮ базу реестра, прошла бы сердцевину.

    Она же — самый дорогой из возможных промахов: два инстанса на одной базе
    показывают одному клиенту переписку другого.
    """
    mod = _launcher()
    root = _root_with(tmp_path, FARM)
    _k1, a = _instance_db(mod, DIVERGING_SLUG, root, monkeypatch)
    _k2, b = _instance_db(mod, COINCIDING_SLUG, root, monkeypatch)
    assert not _same_file(a, b), (
        "оба инстанса получили ОДНУ базу %r: панель клиента показала бы чужую "
        "переписку" % (a,))


# ══ §6.9: слаг без `db:` — ОТКАЗ, а не умолчание ══════════════════════════
NO_DB_FARM = [(DIVERGING_SLUG, True, _MISSING, PORT_A),
              (COINCIDING_SLUG, True, COINCIDING_DB, PORT_B)]


def test_a_slug_without_a_db_line_is_refused(tmp_path, monkeypatch):
    """🔴 СЕРДЦЕВИНА §6.9 дословно: «отсутствие `db:` = ОТКАЗ поднимать».

    🔴 И ЭТО НЕ ФОРМАЛЬНОСТЬ. `chatter.core.client_registry.parse_registry` САМА
    подставляет `.secrets/<slug>.db`, когда `db:` в записи нет, — это
    объявленный контракт реестра («минимальная запись — две строки»). То есть
    реализация, честно взявшая базу «из реестра» этим разбором, привезёт РОВНО
    то умолчание, которым дефект и приехал, и все сторожа §6.8 при этом
    останутся зелёными: у слага с объявленной базой всё верно.
    """
    mod = _launcher()
    root = _root_with(tmp_path, NO_DB_FARM)
    kind, value = _instance_db(mod, DIVERGING_SLUG, root, monkeypatch)
    assert kind == "refused", (
        "у слага %s в реестре НЕТ строки `db:`, а инстанс собрался с базой %r. "
        "Умолчание здесь — ровно тот путь, которым дефект и приехал (§3b)"
        % (DIVERGING_SLUG, value))


def test_the_refusal_is_not_the_slug_derived_default(tmp_path, monkeypatch):
    """Отказ обязан быть ОТКАЗОМ, а не тихой подстановкой.

    Названо отдельно, потому что это единственный исход, который спека
    запрещает поимённо: `.secrets/<slug>.db` при отсутствующем `db:`.
    """
    mod = _launcher()
    root = _root_with(tmp_path, NO_DB_FARM)
    _kind, value = _instance_db(mod, DIVERGING_SLUG, root, monkeypatch)
    assert not _same_file(value, _slug_derived(root, DIVERGING_SLUG)), (
        "при отсутствующем `db:` инстанс получил выведенное из слага %r — то "
        "самое умолчание, которое §3b запрещает" % (value,))


def test_the_refusal_reaches_the_launchers_refusal_path(tmp_path, monkeypatch):
    """Отказ обязан ДОЕХАТЬ до печатного «ОТКАЗ, инстанс не поднят».

    Форма не пинится: исключение — законный отказ. Но если окружение всё же
    собралось, оно обязано быть отвергнуто `instance_env_problems`, и жалоба
    обязана назвать `TAMAPI_DB`. Именно `TAMAPI_DB`, а не «жалобы вообще»:
    жалоба на ненастроенный ключ есть во временном корне ВСЕГДА, и сторож на
    «список непуст» был бы зелёным по построению.
    """
    from app.panel_client import instance_env_problems

    mod = _launcher()
    root = _root_with(tmp_path, NO_DB_FARM)
    kind, payload = _call_build_env(mod, DIVERGING_SLUG, root, monkeypatch)
    if kind == "raised":
        return  # исключение — законная форма отказа
    problems = instance_env_problems(payload)
    assert any("TAMAPI_DB" in p for p in problems), (
        "окружение слага без `db:` собралось и НЕ отвергнуто по TAMAPI_DB: "
        "жалобы %s, окружение %s. Панель поднялась бы молча" % (problems, payload))


def test_a_slug_missing_from_the_registry_entirely_is_refused(tmp_path, monkeypatch):
    """Записи нет вовсе — тем более отказ.

    Слаг, которого реестр не знает, не имеет `db:` в самом сильном смысле. Если
    он получает `.secrets/<slug>.db`, то опечатка в `-Slug` поднимает панель на
    пустой базе и выглядит как исправный старт.
    """
    mod = _launcher()
    root = _root_with(tmp_path, FARM)
    kind, value = _instance_db(mod, "opechatka", root, monkeypatch)
    assert kind == "refused", (
        "слаг, которого нет в реестре, получил базу %r вместо отказа" % (value,))


def test_a_registry_that_cannot_be_read_is_a_refusal_not_a_default(tmp_path, monkeypatch):
    """Реестра нет — отказ, а не «ну тогда по слагу».

    Тот же довод, что у §6.2 в watchdog: «спросить некого» обязано быть
    громким. Умолчание при непрочитанном реестре — это способ поднять инстанс
    ровно тогда, когда источник правды недоступен.
    """
    mod = _launcher()
    root = _root_with(tmp_path, FARM)
    (root / REGISTRY_REL).unlink()
    kind, value = _instance_db(mod, DIVERGING_SLUG, root, monkeypatch)
    assert kind == "refused", (
        "реестра нет, а инстанс собрался с базой %r — источник правды "
        "недоступен, и именно поэтому подниматься нельзя" % (value,))


def test_the_refusal_does_not_swallow_the_declared_neighbour(tmp_path, monkeypatch):
    """Встречная половина §6.9, без неё `raise` в первой строке был бы зелёным.

    В одном реестре два клиента: у одного `db:` нет, у другого есть. Первый —
    отказ, второй — работает. Отказ, накрывающий обоих, погасил бы ЖИВУЮ панель
    yarina правкой чужой записи.
    """
    mod = _launcher()
    root = _root_with(tmp_path, NO_DB_FARM)
    kind, value = _instance_db(mod, COINCIDING_SLUG, root, monkeypatch)
    assert kind == "ok", (
        "сосед с объявленной базой тоже получил отказ (%s): испорченная запись "
        "одного клиента гасит панель другого" % value)
    assert _same_file(value, root / COINCIDING_DB), (
        "сосед получил базу %r вместо объявленной %s" % (value, COINCIDING_DB))


def test_an_empty_db_value_is_refused_too(tmp_path, monkeypatch):
    """`db: ""` — это не путь.

    Пустое значение отличается от отсутствующей строки только на глаз: оба
    означают «база не объявлена», и оба обязаны кончиться одинаково.
    """
    mod = _launcher()
    root = _root_with(tmp_path, [(DIVERGING_SLUG, True, None, PORT_A),
                                 (COINCIDING_SLUG, True, COINCIDING_DB, PORT_B)])
    kind, value = _instance_db(mod, DIVERGING_SLUG, root, monkeypatch)
    assert kind == "refused", (
        "пустое значение `db:` принято за путь: инстанс собрался с %r" % (value,))


# ══ БОЕВОЙ РЕЕСТР: пин на само расхождение и на согласие ══════════════════
def _live_clients() -> dict:
    data = yaml.safe_load(REGISTRY.read_text(encoding="utf-8")) or {}
    return {str(s): (c or {}) for s, c in (data.get("clients") or {}).items()
            if isinstance(c, dict)}


def test_the_live_registry_still_carries_the_divergence_this_pin_stands_on():
    """🔴 ПРЕДУСЛОВИЕ ВСЕХ СТОРОЖЕЙ ФАЙЛА, названное вслух.

    Пин стоит на расхождении слага и базы у volska. Если однажды кто-нибудь
    «приведёт реестр в порядок» — переименует базу в `.secrets/volska.db` или
    выведет её из слага, — расхождение исчезнет, и ВСЕ сторожа §6.8 станут
    зелёными по построению, ничего при этом не проверяя. Такое изменение
    обязано быть громким здесь, а не тихим через полгода.
    """
    clients = _live_clients()
    entry = clients.get(DIVERGING_SLUG)
    assert entry is not None, (
        "в боевом реестре больше нет клиента %s — предмет арки исчез"
        % DIVERGING_SLUG)
    declared = str(entry.get("db") or "")
    assert declared, (
        "у %s в боевом реестре пропала строка `db:` — по §3b это ОТКАЗ "
        "поднимать инстанс, и приёмка арки станет невозможной" % DIVERGING_SLUG)
    assert os.path.basename(declared) != "%s.db" % DIVERGING_SLUG, (
        "слаг и база у %s СОВПАЛИ (%s): расхождение, на котором стоят сторожа "
        "§6.8, исчезло, и они стали зелёными по построению. Пин на совпадении "
        "не отличает чтение реестра от вывода из слага" % (DIVERGING_SLUG, declared))


def test_the_live_registry_names_a_db_for_every_enabled_client():
    """§6.9 на боевом файле: у каждого включённого клиента `db:` ОБЪЯВЛЕН.

    Клиент, которому базу выводят из слага, — это инстанс, который по §3b
    подниматься не имеет права. Узнать об этом надо здесь, а не при приёмке.
    """
    silent = sorted(s for s, c in _live_clients().items()
                    if c.get("enabled") and not str(c.get("db") or "").strip())
    assert not silent, (
        "у включённых клиентов %s в реестре нет `db:` — их базу пришлось бы "
        "выводить из слага, а это ровно запрещённый §3b путь" % silent)


@pytest.mark.parametrize("slug", sorted(s for s, c in _live_clients().items()
                                        if c.get("enabled")))
def test_the_launcher_agrees_with_the_live_registry_about_the_db(slug, monkeypatch):
    """Согласие launcher'а с БОЕВЫМ реестром — по каждому включённому клиенту.

    🟡 Случай `yarina` здесь зелен по построению (совпадение), и он оставлен
    сознательно: разъехавшись, он назовёт клиента поимённо. Работает же этот
    сторож случаем `volska` — тем самым, ради которого написана ПОПРАВКА 1.

    Дерево не трогается: читается реестр, зовётся чистая функция. Существование
    файла базы здесь НЕ проверяется — `.secrets` в worktree нет вовсе, и такая
    проверка мерила бы среду, а не код ([[jarvis-worktree-missing-gitignored-
    client-config]]).
    """
    mod = _launcher()
    declared = str(_live_clients()[slug].get("db") or "")
    kind, value = _instance_db(mod, slug, REPO_ROOT, monkeypatch)
    assert kind == "ok", (
        "инстанс включённого клиента %s не собрался: %s" % (slug, value))
    assert _same_file(value, REPO_ROOT / declared), (
        "launcher отдаёт инстансу %s базу %r, а боевой реестр объявляет %s. "
        "Панель показала бы не ту переписку, которую ведёт раннер этого "
        "клиента" % (slug, value, declared))
