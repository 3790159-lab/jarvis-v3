# -*- coding: utf-8 -*-
"""ВЕБ, волна 2 / пара D: сторожа §5 спеки `2026-08-28-web-d-panel-send.md`.

СТОРОЖА ПИСАНЫ ОТ СПЕКИ И ДО КОДА ([[jarvis-guards-not-by-the-plan-author]]).
Кода арки не существует; автор этих сторожей его не видел и не искал. Читались
только ЖИВЫЕ места, которые арка режет (`deliver_outgoing`, `open_human_takeover`,
`Store.enqueue_outgoing/begin_takeover/mute`, `chatter/transport/base.py`,
`app/panel_client.py`, `app/routers/tamapi_dashboard.py`) — потому что арка
правит живой код, а не пишет с нуля.

🔴 ГЛАВНОЕ ТРЕБОВАНИЕ ЭТОГО ФАЙЛА — КАНАЛО-НЕЗАВИСИМОСТЬ. Сторож, который
умеет только `web`, придётся переписывать на Instagram, — значит это не сторож,
а отсрочка. Поэтому ни один сценарий ниже не построен на Telegram и ни один не
построен на `web`: полный путь задания гоняется через ВЫМЫШЛЕННЫЙ канал
`moonmail`, которого в дереве нет и не будет. Реализация, знающая `web`
поимённо, зелёной здесь не станет.

🔴 ЧТО ИЗМЕНИЛ МЕРЖ ПАРЫ C (29.08). Канал перестал быть свободной строкой:
`contact_ref.KNOWN_CHANNELS` — ЛИТЕРАЛЬНЫЙ реестр, и голова, которой в нём нет,
отвергается fail-closed (спека C §3.1). Значит цена нового канала — не одна
строка, а ДВЕ, и обе литеральные: запись в реестр форм и регистрация доставщика.
Это не ослабление §2.1 пары D, а его уточнение: «одна регистрация» относится к
ДОСТАВКЕ, реестр форм отвечает на другой вопрос — «существует ли такой адрес
вообще». Выведи реестр из регистраций — и он согласится с кодом по определению,
промолчав ровно там, где код забыл ([[jarvis-literal-lists-not-introspection]]).

Поэтому `_register` ниже объявляет вымышленный канал В ОБОИХ местах и обе
записи откатывает. Тест, который обошёл бы реестр монкипатчем одного разбора,
доказывал бы не канало-независимость, а собственную изобретательность.

🔴 ЖИВОГО НИЧЕГО НЕ ТРОГАЕТСЯ. Ни Telegram, ни `.secrets/*.db`: база — файл в
`tmp_path`, доставщик — двойник, транспорт — `FakeConsoleTransport`. Повод
назван вслух: на живом транспорте в этом доме уже будили владельца 25 вопросами
за 7 минут ([[jarvis-hook-tests-hit-live-telegram]]).

⚠️ ИМЕНА, КОТОРЫХ СПЕКА НЕ НАЗЫВАЕТ. Спека называет литерально `deliverer_for`,
`SentRef`, `can_send_now`, адрес `…/d/<contact_id>` и поле `event_token`; НЕ
называет ни модуль ядра («живёт в `chatter/` вне `telethon_run.py`»), ни имя
точки входа ядра, ни способ регистрации доставщика, ни форму фильтра очереди по
каналу. Догадки собраны в ЛИТЕРАЛЬНЫЕ списки-кандидаты в одном месте (блок
«разведка контракта») — чтобы при расхождении правилась ОДНА таблица, а красный
называл невыполненный пункт спеки, а не ронял `ImportError` из глубины.
"""
from __future__ import annotations

import ast
import asyncio
import importlib
import inspect
import random
import re
from pathlib import Path

import pytest

import chatter.telethon_run as tr
from chatter.config.loader import load_config
from chatter.core.brain import Brain, build_messages
from chatter.core.llm import FakeLLM
from chatter.core.pause import human_holds_dialog, is_muted
from chatter.notify.base import Action
from chatter.notify.control_bot import route_callback
from chatter.run import Deps, process_batch
from chatter.storage.db import Store
from chatter.transport.fake import FakeConsoleTransport

REPO_ROOT = Path(__file__).resolve().parents[2]
CLIENTS_DIR = REPO_ROOT / "chatter" / "clients"
RUNNER_SRC = REPO_ROOT / "chatter" / "telethon_run.py"
PANEL_CLIENT_SRC = REPO_ROOT / "app" / "panel_client.py"
DASHBOARD_SRC = REPO_ROOT / "app" / "routers" / "tamapi_dashboard.py"
CONTACT_REF_SRC = REPO_ROOT / "chatter" / "core" / "contact_ref.py"

SLUG = "demo"
# 🔴 ФОРМА С КАНАЛОМ (мерж пары C, 29.08). До него здесь стояло `"111:demo"` —
# сегодняшняя двухсегментная форма. Пара C её ОТВЕРГАЕТ (§13.1): совместимость
# живёт ровно в одном месте, `upgrade_legacy_callback_contact`, и только ради
# кнопок, отправленных ДО миграции. Тесты, продолжавшие писать старую форму,
# проверяли бы адрес, которого в дереве больше нет.
TG = "telegram:111:demo"
# Свободный диалог: без него «бот молчит» неотличимо от «бота выключили везде».
FREE = "telegram:222:demo"
# 🔴 ВЫМЫШЛЕННЫЙ канал. Ни `web`, ни `instagram`: сторож обязан краснеть на
# реализации, которая выучила названия каналов наизусть.
MOON = "moonmail:ext-77:demo"
MOON_CHANNEL = "moonmail"
# Канал, у которого доставщика НЕТ и в паре D не будет (§2.6).
WEB = "web:sess-1:demo"

HOUR = 3600.0

# §2.4: порядок силы источников паузы объявлен ЛИТЕРАЛЬНО, а не выведен.
WEAK_PAUSE_SOURCE = "command"
STRONG_PAUSE_SOURCE = "human_takeover"

# §2.6 + §4: сегодня зарегистрирован ровно один канал. Список литеральный и
# проверяется в ОБЕ стороны ([[jarvis-literal-lists-not-introspection]]).
REGISTERED_CHANNELS_TODAY = ("telegram",)
NOT_REGISTERED_CHANNELS = ("web", "instagram", "whatsapp", "viber")


# ═══ разведка контракта: ЛИТЕРАЛЬНЫЕ кандидаты имён ═════════════════════════
#
# Каждый список — догадка о том, чего спека не назвала. Красный от них обязан
# называть ПУНКТ СПЕКИ, а не «нет модуля X».

CORE_MODULE_CANDIDATES = (
    "chatter.core.outgoing", "chatter.core.delivery", "chatter.core.deliver",
    "chatter.core.outgoing_core", "chatter.core.send",
    "chatter.outgoing", "chatter.delivery",
)
DELIVER_ENTRY_CANDIDATES = (
    "deliver", "deliver_one", "deliver_row", "deliver_job", "deliver_queued",
    "deliver_outgoing",
)
REGISTER_CANDIDATES = ("register_deliverer", "register_channel",
                       "add_deliverer", "register")
REGISTRY_CANDIDATES = ("DELIVERERS", "_DELIVERERS", "DELIVERER_REGISTRY",
                       "_REGISTRY", "REGISTRY")

_SPEC = "docs/superpowers/specs/2026-08-28-web-d-panel-send.md"


def _core():
    """Ядро доставки (§2.1) или ГРОМКИЙ отказ с адресом договора."""
    tried = []
    for name in CORE_MODULE_CANDIDATES:
        try:
            mod = importlib.import_module(name)
        except Exception as exc:            # noqa: BLE001
            tried.append("%s (%s)" % (name, type(exc).__name__))
            continue
        if getattr(mod, "deliverer_for", None) is not None:
            return mod
        tried.append("%s (есть, но без deliverer_for)" % name)
    raise AssertionError(
        "§2.1 %s: ядра доставки нет. Доставка обязана разрезаться на ядро "
        "(канало-независимое, живёт в `chatter/` ВНЕ `telethon_run.py`) и "
        "доставщика канала `deliverer_for(channel)`. Искал: %s"
        % (_SPEC, ", ".join(tried)))


def _core_attr(name: str, why: str):
    mod = _core()
    obj = getattr(mod, name, None)
    assert obj is not None, (
        "в ядре доставки (%s) нет `%s` — %s (спека %s)"
        % (mod.__name__, name, why, _SPEC))
    return obj


def _deliver_entry():
    mod = _core()
    for name in DELIVER_ENTRY_CANDIDATES:
        fn = getattr(mod, name, None)
        if callable(fn):
            return fn
    raise AssertionError(
        "§2.1 %s: у ядра доставки нет точки входа «доставить ОДНУ строку "
        "очереди» (искал %s). Без неё полный путь задания проверить нечем"
        % (_SPEC, ", ".join(DELIVER_ENTRY_CANDIDATES)))


class _Ctx:
    """Контекст процесса для ядра: персоны и store. Имя аргумента спекой не
    названо, поэтому объект отдаётся во все известные формы вызова."""

    def __init__(self, store, personas):
        self.personas = personas
        self._store = store
        self.primary_slug = SLUG

    def primary_store(self):
        return self._store


def _await(value):
    if inspect.isawaitable(value):
        return asyncio.run(_as_coro(value))
    return value


async def _as_coro(value):
    return await value


def _deliver(store, row, *, now, personas):
    """Прогнать ОДНУ строку очереди через ядро. Возвращает вердикт.

    Форма вызова спекой не названа — перебираются известные формы, и если ни
    одна не подходит, отказ называет их все."""
    fn = _deliver_entry()
    ctx = _Ctx(store, personas)
    shapes = (
        ("f(ctx, row, now=now)", lambda: fn(ctx, row, now=now)),
        ("f(store=store, row=row, now=now)",
         lambda: fn(store=store, row=row, now=now)),
        ("f(ctx=ctx, row=row, now=now)", lambda: fn(ctx=ctx, row=row, now=now)),
        ("f(store, row, now=now)", lambda: fn(store, row, now=now)),
        ("f(store, row, now)", lambda: fn(store, row, now)),
        ("f(row, store=store, now=now)",
         lambda: fn(row, store=store, now=now)),
    )
    problems = []
    for label, call in shapes:
        try:
            return _await(call())
        except TypeError as exc:
            problems.append("%s -> TypeError: %s" % (label, exc))
    raise AssertionError(
        "точка входа ядра `%s` не зовётся ни одной известной формой: %s "
        "(спека %s §2.1)" % (getattr(fn, "__name__", fn),
                             " | ".join(problems), _SPEC))


class _Deliverer:
    """Доставщик ВЫМЫШЛЕННОГО канала — двойник, а не мок.

    🔴 ЭХА НЕ РЕГИСТРИРУЕТ НАМЕРЕННО (§2.3, §5 п.4): в вебе и бизнес-API эха
    не существует по построению, и инвариант «одно задание = одна строка в
    `messages` и один `sent`» обязан держаться БЕЗ `sent_registry`. Двойник,
    глушащий собственное эхо, проверял бы реестр, а не инвариант.

    Явный класс, а не `MagicMock`: автомок истинен всегда и отвечает «да» на
    любой вопрос ([[jarvis-magicmock-truthy-spins-the-loop]]).
    """

    def __init__(self, *, msg_id=None, is_async=False, raises=None):
        self.calls: list[tuple] = []
        self.msg_id = msg_id
        self.is_async = is_async
        self.raises = raises

    def _do(self, args, kwargs):
        self.calls.append((args, kwargs))
        if self.raises is not None:
            raise self.raises
        return _sent_ref(self.msg_id)

    def __call__(self, *args, **kwargs):
        if self.is_async:
            return self._async(args, kwargs)
        return self._do(args, kwargs)

    async def _async(self, args, kwargs):
        return self._do(args, kwargs)

    def saw(self, text: str) -> int:
        return sum(1 for a, k in self.calls if text in repr((a, k)))


def _sent_ref(msg_id):
    """`SentRef(msg_id: str | None)` — §2.2, имя названо спекой литерально."""
    SentRef = _core_attr(
        "SentRef",
        "§2.2 требует, чтобы доставщик отдавал `SentRef(msg_id: str | None)`, "
        "а `Transport` при этом НЕ расширялся шестым abstract-методом")
    for build in (lambda: SentRef(msg_id=msg_id), lambda: SentRef(msg_id)):
        try:
            return build()
        except TypeError:
            continue
    raise AssertionError(
        "`SentRef` не собирается ни как SentRef(msg_id=...), ни как "
        "SentRef(...): §2.2 называет ровно одно поле — `msg_id`")


def _declare_channel(channel: str):
    """Объявить канал в ЛИТЕРАЛЬНОМ реестре форм. Возвращает функцию отката.

    После мержа пары C `contact_ref` отвергает голову, которой в реестре нет
    (§3.1), и вымышленный `moonmail` без этой записи не собирается в
    `contact_id` вовсе — то есть до доставщика дело не доходит. Запись
    временная и снимается откатом: реестр — предмет продукта, а не теста.
    """
    from chatter.core import contact_ref
    before = contact_ref.KNOWN_CHANNELS
    if channel in before:
        return lambda: None
    contact_ref.KNOWN_CHANNELS = tuple(before) + (channel,)

    def undo():
        contact_ref.KNOWN_CHANNELS = before

    return undo


@pytest.fixture(autouse=True)
def _moon_channel_exists():
    """Вымышленный канал объявлен в реестре форм НА ВСЁ ВРЕМЯ теста.

    Почему автоиспользуемой фикстурой, а не только внутри `_register`. Реестр
    форм (`contact_ref.KNOWN_CHANNELS`) отвечает на вопрос «существует ли такой
    АДРЕС», а регистрация доставщика — на вопрос «есть ли чем послать». Это
    разные вопросы, и половина сценариев ниже задаёт первый, не задавая второго:
    страница диалога открывается по адресу и обязана открыться ДО того, как
    кто-нибудь зарегистрировал доставщика (§2.6 — «нельзя послать» говорится
    ПОСЛЕ показа страницы, а не вместо неё).

    Без этой фикстуры такие сценарии получали бы 404 — и 404 читался бы как
    «страницы нет», хотя на самом деле это «канала не существует». Два разных
    отказа под одним кодом — ровно тот класс, который эта арка и разбирает.
    """
    undo = _declare_channel(MOON_CHANNEL)
    try:
        yield
    finally:
        undo()


def _register(channel: str, deliverer):
    """Зарегистрировать доставщик канала. Возвращает функцию отката.

    Способ регистрации спекой не назван; §2.1 называет СВОЙСТВО: добавление
    канала обязано стоить ОДНУ регистрацию — плюс, с мержа пары C, одну запись
    в литеральном реестре форм (см. шапку файла)."""
    undo_decl = _declare_channel(channel)
    mod = _core()
    for name in REGISTER_CANDIDATES:
        fn = getattr(mod, name, None)
        if callable(fn):
            for call in (lambda: fn(channel, deliverer),
                         lambda: fn(channel=channel, deliverer=deliverer)):
                try:
                    call()
                except TypeError:
                    continue
                return lambda: (_unregister(channel), undo_decl())
    for name in REGISTRY_CANDIDATES:
        reg = getattr(mod, name, None)
        if isinstance(reg, dict):
            reg[channel] = deliverer
            return lambda: (reg.pop(channel, None), undo_decl())
    raise AssertionError(
        "§2.1 %s: канал не регистрируется НИ ОДНОЙ строкой — нет ни функции "
        "регистрации (%s), ни реестра (%s). «Добавление Instagram стоит одну "
        "регистрацию» проверить нечем"
        % (_SPEC, ", ".join(REGISTER_CANDIDATES), ", ".join(REGISTRY_CANDIDATES)))


def _unregister(channel: str):
    mod = _core()
    for name in ("unregister_deliverer", "unregister_channel", "unregister"):
        fn = getattr(mod, name, None)
        if callable(fn):
            try:
                fn(channel)
                return
            except TypeError:
                pass
    for name in REGISTRY_CANDIDATES:
        reg = getattr(mod, name, None)
        if isinstance(reg, dict):
            reg.pop(channel, None)


def _pending_for(store, channels):
    """`pending_outgoing`, ограниченная каналами ЭТОГО процесса (§2.8)."""
    chans = tuple(channels)
    shapes = [("channels=%r" % (chans,), lambda: store.pending_outgoing(channels=chans)),
              ("channels=%r" % (list(chans),),
               lambda: store.pending_outgoing(channels=list(chans)))]
    if len(chans) == 1:
        shapes.append(("channel=%r" % chans[0],
                       lambda: store.pending_outgoing(channel=chans[0])))
    problems = []
    for label, call in shapes:
        try:
            return call()
        except TypeError as exc:
            problems.append("%s -> %s" % (label, exc))
    raise AssertionError(
        "§2.8 %s: у `pending_outgoing` нет фильтра по каналу (%s). Значит два "
        "процесса читают ОДНИ И ТЕ ЖЕ `pending` без блокировки и отправят "
        "задание дважды" % (_SPEC, " | ".join(problems)))


# ═══ общая оснастка ═════════════════════════════════════════════════════════

@pytest.fixture()
def store(tmp_path):
    """База — файл в `tmp_path`. Живой `.secrets/` не трогается никогда."""
    s = Store(str(tmp_path / "web_d.db"))
    for cid in (TG, FREE, MOON, WEB):
        s.get_or_create_contact(cid)
    yield s
    s.close()


def _persona(store, *, clock=1000.0, scripted=None):
    cfg = load_config(CLIENTS_DIR, SLUG)
    return Deps(cfg=cfg, store=store, brain=Brain(FakeLLM(scripted=scripted), cfg),
                rng=random.Random(0), clock=lambda: clock, sleep=lambda _s: None)


def _personas(store):
    cfg = load_config(CLIENTS_DIR, SLUG)
    bundle = tr.PersonaBundle(cfg=cfg, deps=_persona(store))
    return {SLUG: bundle}


def _enqueue(store, contact_id, text, *, token, now=1000.0):
    return store.enqueue_outgoing(contact_id, text, token=token, now=now)


def _row(store, contact_id):
    r = store._conn.execute(
        "SELECT * FROM outgoing_queue WHERE contact_id=? ORDER BY id",
        (contact_id,)).fetchall()
    return [dict(x) for x in r]


def _contact(store, contact_id) -> dict:
    return dict(store.get_or_create_contact(contact_id))


def _bot_turn(store, contact_id, *, reply, clock=1000.0):
    """Один ход БОТА. Возвращает транспорт-двойник (ноль сети)."""
    deps = _persona(store, clock=clock, scripted=[reply])
    transport = FakeConsoleTransport(preload=[], echo=False)
    process_batch(contact_id, ["а сколько стоит?"], transport, deps)
    return transport


def _through_core(store, contact_id, text, *, token, now=1000.0,
                  channel=MOON_CHANNEL, msg_id=None):
    """Полный путь задания по ВЫМЫШЛЕННОМУ каналу. Возвращает (вердикт, доставщик).

    Синхронный доставщик пробуется первым, асинхронный — вторым: форма спекой
    не названа, а сторож обязан краснеть от ОТСУТСТВИЯ поведения, а не от
    угаданной формы вызова."""
    _enqueue(store, contact_id, text, token=token, now=now)
    row = [r for r in store.pending_outgoing() if r["contact_id"] == contact_id][0]
    last = None
    for is_async in (False, True):
        deliverer = _Deliverer(msg_id=msg_id, is_async=is_async)
        undo = _register(channel, deliverer)
        try:
            verdict = _deliver(store, row, now=now, personas=_personas(store))
        except (TypeError, AssertionError) as exc:
            last = exc
            continue
        finally:
            undo()
        return verdict, deliverer
    raise last if last is not None else AssertionError("недостижимо")


# ═══ §5 п.1: ЯДРО ДОСТАВКИ НЕ ИМПОРТИРУЕТ TELETHON ══════════════════════════

_TELEGRAM_MARKERS = ("telethon", "TelethonTransport", "get_input_entity",
                     "access_hash", "InputPeer", "FloodWait")
_CHANNEL_LITERALS = ("web", "telegram", "instagram", "whatsapp", "viber")


def _core_source() -> tuple[str, str]:
    mod = _core()
    path = inspect.getsourcefile(mod)
    assert path, "у ядра доставки нет исходника на диске"
    p = Path(path)
    assert p.resolve() != RUNNER_SRC.resolve(), (
        "§2.1 %s: ядро доставки обязано жить ВНЕ `chatter/telethon_run.py`, а "
        "оно там" % _SPEC)
    return p.name, p.read_text(encoding="utf-8")


def test_yadro_dostavki_ne_znaet_pro_telethon():
    """§5 п.1. Ядро — канало-независимое; Telethon в нём не существует.

    Пин по AST и по тексту сразу: `import telethon` ловит AST, `runner.client`
    и `TelethonTransport` — обращение по имени. Ядро, притащившее транспорт
    «на минуточку», перестаёт быть ядром в тот же день, а заметно это станет
    на втором канале — то есть через месяцы.
    """
    name, src = _core_source()
    tree = ast.parse(src)

    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    assert not [m for m in imported if "telethon" in m.lower()], (
        "ядро доставки (%s) импортирует Telethon: %r — значит веб-процесс, у "
        "которого раннера нет вовсе, ядро даже не загрузит" % (name, imported))

    for marker in _TELEGRAM_MARKERS:
        assert marker not in src, (
            "в ядре доставки (%s) встречается телеграмное `%s`: §2.1 требует "
            "единственного места, знающего про peer и access_hash, — им обязан "
            "быть ДОСТАВЩИК канала, а не ядро" % (name, marker))

    assert "runner." not in src, (
        "ядро доставки (%s) обращается к `runner.`: у веб-процесса раннера нет "
        "вовсе (§1.2 п.1, строка 1982 живого кода)" % name)


def test_yadro_ne_sravnivaet_nazvanie_kanala_s_literalom():
    """§5 п.1, вторая половина: «сторож краснеет от одной строки
    `if channel == "web"` в ядре».

    Это и есть признак провала, объявленный §1 спеки веба: код, который знает
    про `web` и не знает про «канал вообще». Ловится по AST-сравнениям с
    литералом, а не по подстроке: подстрока покраснела бы и на комментарии.
    """
    name, src = _core_source()
    guilty = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Compare):
            continue
        for side in [node.left] + list(node.comparators):
            if isinstance(side, ast.Constant) and side.value in _CHANNEL_LITERALS:
                guilty.append(side.value)
        for side in node.comparators:
            if isinstance(side, (ast.Tuple, ast.List, ast.Set)):
                for elt in side.elts:
                    if isinstance(elt, ast.Constant) and elt.value in _CHANNEL_LITERALS:
                        guilty.append(elt.value)
    assert not guilty, (
        "ядро доставки (%s) сравнивает канал с литералами %r: добавление "
        "Instagram будет стоить правку ЯДРА, а не одну регистрацию (§2.1)"
        % (name, sorted(set(guilty))))


# ═══ §5 п.2: ДОБАВЛЕНИЕ КАНАЛА СТОИТ ОДНУ РЕГИСТРАЦИЮ ═══════════════════════

def test_vymyshlennyi_kanal_edet_polnyi_put_ODNOI_registraciei(store):
    """§5 п.2. Регистрирую доставщик канала, которого в дереве НЕТ, и гоняю
    задание полным путём, не тронув ни строки ядра.

    🔴 Канал ВЫМЫШЛЕН намеренно. На `web` этот сторож зеленел бы у реализации,
    которая выучила слово `web` наизусть, — то есть у ровно той, от которой
    спека и отказывается (§2.1). «Сколько строк тронуть, чтобы добавить
    Instagram» — вопрос, на который ответ обязан быть «одну регистрацию», и
    измеряется он только неизвестным каналом.
    """
    verdict, deliverer = _through_core(
        store, MOON, "письмо по лунной почте", token="tok-moon-1",
        msg_id="moon-abc")

    assert verdict == "sent", (
        "задание в канал `%s` дало вердикт %r: одной регистрации доставщика "
        "не хватило, значит ядро знает каналы поимённо (§2.1)"
        % (MOON_CHANNEL, verdict))
    assert deliverer.saw("письмо по лунной почте") == 1, (
        "доставщик вымышленного канала не увидел текст задания: %r"
        % (deliverer.calls,))
    rows = _row(store, MOON)
    assert [r["status"] for r in rows] == ["sent"], rows


def test_spisok_zaregistrirovannyh_kanalov_LITERALEN_v_obe_storony():
    """§5 п.2, вторая половина: литеральный список — в ОБЕ стороны.

    Выведенный из кода список согласен с кодом по определению и промолчит там,
    где код забыл ([[jarvis-literal-lists-not-introspection]]). Обратная
    половина не менее важна: §2.6 говорит, что `deliverer_for("web")` в паре D
    НЕ регистрируется, и сторож обязан краснеть на реализации, которая
    зарегистрировала пустышку, чтобы «не отказывать».
    """
    deliverer_for = _core_attr(
        "deliverer_for", "§2.1 называет реестр доставщиков этим именем")
    for channel in REGISTERED_CHANNELS_TODAY:
        assert deliverer_for(channel) is not None, (
            "канал `%s` объявлен работающим, а доставщика у него нет: сегодня "
            "это единственный канал, которым отправка вообще ездит" % channel)
    for channel in NOT_REGISTERED_CHANNELS:
        got = None
        try:
            got = deliverer_for(channel)
        except (KeyError, LookupError, ValueError):
            got = None
        assert got is None, (
            "у канала `%s` ЕСТЬ доставщик (%r), хотя §2.6 требует его "
            "отсутствия: пока волна 3 не построила сессию посетителя, задание "
            "обязано получать громкий отказ, а не уезжать в пустышку"
            % (channel, got))


# ═══ §5 п.3: РАЗБОР ГОЛОВЫ contact_id — ОДИН, ТОТ ЖЕ ЧТО У ВОЛНЫ 1 ══════════

def _delivery_sources() -> list[tuple[str, str]]:
    """Исходники ДОСТАВКИ: ядро + всё «доставочное» в `telethon_run.py`.

    Живой `telethon_run._persona_settings` (`rsplit(":", 1)`) сюда НЕ входит и
    не должен: он вне доставки, и `contact_ref.slug_of` прямо оговаривает его
    как место с фолбэком. Сторож, покрасневший на нём, требовал бы того, чего
    спека не просит.
    """
    out = []
    try:
        # Ядра может ещё не быть — но телеграмная доставка ЖИВЁТ, и дефект
        # §1.2 п.4 надо называть по адресу, а не хоронить под «ядра нет».
        out.append(_core_source())
    except AssertionError:
        pass
    runner_src = RUNNER_SRC.read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(runner_src)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if "outgoing" in node.name or "deliver" in node.name:
                out.append(("telethon_run.%s" % node.name, ast.unparse(node)))
    return out


_PARSE_CALLS = ("rpartition", "rsplit")


def test_v_dostavke_net_svoego_razbora_golovy_contact_id():
    """§5 п.3. Строка 1974 живого кода — ДЕВЯТОЕ место разбора головы, не
    покрытое волной 1 (`rpartition` в шаблон восьми `split` не попал).

    Отказ там громкий, поэтому это не дыра, а НЕВЫПОЛНЕННОЕ ПРЕДУСЛОВИЕ: разбор
    обязан быть один. Сторож смотрит на приёмник вызова — режется именно
    `contact_id`, а не чужая строка вроде `handle.ref`.
    """
    for name, src in _delivery_sources():
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not isinstance(fn, ast.Attribute):
                continue
            recv = ast.unparse(fn.value)
            if "contact" not in recv:
                continue
            assert fn.attr not in _PARSE_CALLS + ("split",), (
                "%s режет `%s` сам (`%s`): волна 1 свела разбор в "
                "`chatter/core/contact_ref.py`, и доставка обязана ходить "
                "через него — иначе на форме `\"<канал>:<id>:<персона>\"` "
                "голова окажется названием канала (§1.2 п.4)"
                % (name, recv, fn.attr))


def test_v_dostavke_net_int_nad_kuskom_contact_id():
    """§5 п.3, вторая половина: `int(...)` над куском id — тот же дефект с
    другого конца.

    `int(peer_part)` (строка 1976 живого кода) — это молчаливое утверждение
    «голова всегда телеграмный peer». `contact_ref.telegram_peer_of` задаёт
    этот вопрос в ОДНОМ месте, и задавать его повторно в доставке нельзя.
    """
    for name, src in _delivery_sources():
        for node in ast.walk(ast.parse(src)):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "int" and node.args):
                continue
            arg = ast.unparse(node.args[0])
            assert not any(k in arg for k in ("contact", "peer_part", "head")), (
                "%s зовёт `int(%s)`: второе место, решающее «это телеграмный "
                "контакт?». Вопрос обязан задаваться там же, где волна 1 — в "
                "`contact_ref.telegram_peer_of` (§5 п.3)" % (name, arg))


def test_mesto_razbora_na_dostavku_ROVNO_ODNO():
    """§5 п.3, третья половина: «мест разбора — ровно одно».

    Доставка обязана ИМПОРТИРОВАТЬ разбор, а не повторять его. Без этой
    половины реализация, вынесшая `rpartition` в свою приватную функцию
    рядышком, прошла бы обе проверки выше.
    """
    name, src = _core_source()
    tree = ast.parse(src)
    uses_contact_ref = any(
        isinstance(n, ast.ImportFrom) and "contact_ref" in (n.module or "")
        for n in ast.walk(tree)) or "contact_ref" in src
    assert uses_contact_ref, (
        "ядро доставки (%s) не ходит в `chatter/core/contact_ref.py` вовсе: "
        "значит разбор `contact_id` у него свой — второе место на дерево "
        "(§5 п.3)" % name)


# ═══ §5 п.4: ОДНО ЗАДАНИЕ = ОДНА СТРОКА В messages И ОДИН sent ══════════════

def test_odno_zadanie_odna_stroka_v_lente_i_odin_sent_BEZ_eha(store):
    """§5 п.4. Проверяется на канале БЕЗ ЭХА — иначе сторож проверяет
    `sent_registry`, а не инвариант.

    🔴 ПОЧЕМУ ИМЕННО ТАК (§2.3). В Telegram задвоения нет не потому, что кто-то
    следит, а потому что `sent_registry` глушит эхо своего же исходящего. В
    вебе и бизнес-API эха не существует ПО ПОСТРОЕНИЮ — там некому задвоить, и
    заводить туда реестр не надо. Инвариант формулируется без слова «канал»:
    одно задание = одна строка в `messages` и одна строка `sent` в очереди. Он
    и пинится.

    Двойник `_Deliverer` отправленное НЕ регистрирует намеренно.
    """
    verdict, deliverer = _through_core(
        store, MOON, "ровно один раз", token="tok-moon-once", msg_id="moon-1")
    assert verdict == "sent", verdict

    assert len(deliverer.calls) == 1, (
        "доставщик позван %d раз(а) на ОДНО задание: %r"
        % (len(deliverer.calls), deliverer.calls))

    lenta = [m for m in store.history(MOON) if m["text"] == "ровно один раз"]
    assert len(lenta) == 1, (
        "одно задание оставило %d строк(и) в `messages`: владелец прочитает "
        "свой же диалог с задвоением, и разбор «кто это написал» упрётся в "
        "догадку; %r" % (len(lenta), lenta))
    assert lenta[0]["role"] == "assistant" and lenta[0]["author"] == "human", (
        "ручная реплика легла в историю как реплика БОТА (%r): роль уходит в "
        "API как есть, а автор живёт рядом" % (lenta[0],))

    rows = _row(store, MOON)
    assert [r["status"] for r in rows] == ["sent"], (
        "в очереди не ровно одна строка `sent`: %r" % (rows,))


def test_povtornyi_krug_sliva_ne_daet_vtoroi_otpravki_na_bezehovom_kanale(store):
    """§5 п.4, парная половина. Второй круг слива обязан не найти работы.

    Без неё «одно задание = одна отправка» зелено на реализации, которая
    просто ещё не успела покрутиться второй раз.
    """
    _through_core(store, MOON, "второго круга не будет",
                  token="tok-moon-loop", msg_id="moon-2")
    left = _pending_for(store, (MOON_CHANNEL,))
    assert left == [], (
        "после успешной отправки задание всё ещё `pending` (%r): следующий тик "
        "отправит его ВТОРОЙ раз" % (left,))


# ═══ §5 п.5: СНУЗ НЕ СЪЕДАЕТ ПЕРЕХВАТ ═══════════════════════════════════════
#
# 🔴 Самое дорогое место арки, и оно уже воспроизводится СМЕРЖЕННЫМ путём
# (§2.4): `begin_takeover` стоит на `WHERE paused=0`, под снузом эпизод не
# открывается, `pause_until` доживает до истечения — и бот говорит поверх
# человека. Сторож ловит именно ЭТОТ ИСХОД, а не наличие поля.

def _human_sends_from_panel(store, contact_id, *, text, now):
    """Человек нажал «отправить в панели» — единственной точкой открытия
    эпизода на всё дерево (§2.3, `open_human_takeover`).

    Канало-независимо по построению: точка не знает ни про peer, ни про
    транспорт — это операция над строкой контакта.
    """
    fn = getattr(tr, "open_human_takeover", None)
    assert fn is not None, (
        "нет `open_human_takeover`: §2.3 объявляет её ЕДИНСТВЕННОЙ точкой "
        "открытия эпизода, и третьего вызывателя пара D не заводит")
    return fn(store, contact_id, msg_id=None, detail=text[:200], now=now)


def test_snuz_NE_SEDAET_perehvat_bot_ne_govorit_poverh_cheloveka(store):
    """§5 п.5. ЛОВИТСЯ ИСХОД, А НЕ ПОЛЕ.

    Сценарий из §2.4, шаг в шаг:
      1. владелец жмёт «⏸ Ще 1год» — `mute(source='command', until=+1ч)`;
      2. через десять минут передумал и нажал «отправить» в панели;
      3. сегодня `begin_takeover` (`WHERE paused=0`) даёт 0 строк, эпизод «не
         наш», `pause_source` остаётся `'command'`, `pause_until` — прежним;
      4. час истёк → `is_muted` False по ветке «истёкший дедлайн»,
         `human_holds_dialog` False (источник не `human_takeover`);
      5. И БОТ ЗАГОВОРИЛ ПОВЕРХ ЧЕЛОВЕКА.

    Последнее утверждение — единственное, ради которого сторож существует.
    Проверка «`pause_source` стал сильным» без него зелена у реализации,
    которая поле поправила, а решение о молчании оставила прежним
    ([[jarvis-two-numbers-for-one-thing]]).
    """
    t0, snooze_until = 900.0, 900.0 + HOUR
    store.mute(TG, source=WEAK_PAUSE_SOURCE, until=snooze_until, now=t0)
    assert _contact(store, TG)["pause_until"] == snooze_until, (
        "предпосылка: снуз стоит и у него есть срок")

    _human_sends_from_panel(store, TG, text="я отвечу сам", now=t0 + 600.0)

    # 🔴 ИСХОД — ПЕРВЫМ УТВЕРЖДЕНИЕМ. Проверки полей ниже нужны, чтобы отличить
    # починку от совпадения, но красный обязан читаться словами «бот заговорил
    # поверх человека», а не «поле не то».
    after = snooze_until + 60.0
    spoke = _bot_turn(store, TG, reply="БОТ НЕ ИМЕЕТ ПРАВА ЭТО СКАЗАТЬ",
                      clock=after)
    assert spoke.sent == [], (
        "🔴 БОТ ЗАГОВОРИЛ ПОВЕРХ ЧЕЛОВЕКА после истечения съеденного снуза: "
        "%r. Это ровно тот исход, ради которого пара D повышает силу паузы "
        "(§2.4)" % (spoke.sent,))

    # ПАРНАЯ ПОЛОВИНА: молчание получено ПОВЫШЕНИЕМ СИЛЫ ПАУЗЫ, а не тем, что
    # бота заглушили везде. Без неё сторож зелен у реализации, взведшей
    # `kill_switch` (или сломавшей ход) — а это тишина на всей воронке.
    free = _bot_turn(store, FREE, reply="Добрый день. Чем помочь?", clock=after)
    assert free.sent, (
        "бот замолчал и в диалоге, где человека нет: перехват одного контакта "
        "выключил всю воронку")

    row = _contact(store, TG)
    assert row["pause_source"] == STRONG_PAUSE_SOURCE, (
        "после отправки человеком источник паузы остался %r: снуз СЪЕЛ "
        "перехват — эпизод не открылся, потому что `begin_takeover` стоит на "
        "`WHERE paused=0` (§2.4)" % (row["pause_source"],))
    assert row["pause_until"] is None, (
        "срок снуза (%r) пережил отправку человеком: он доживёт до истечения и "
        "разморозит диалог под человеком" % (row["pause_until"],))
    assert row["paused"] == 1, (
        "диалог не заглушён после отправки человеком: %r" % (row,))
    assert is_muted(row, kill_switch=False, now=after), (
        "через час после снуза бот считает диалог свободным — а его ведёт "
        "человек")
    assert human_holds_dialog(row), (
        "`human_holds_dialog` False: по журналу диалог не за человеком, и "
        "лечиться он будет таймером вместо «человек договорил»")


def test_snuz_ne_sedaet_perehvat_i_KOGDA_epizod_uzhe_shel(store):
    """§5 п.5, вторая половина §2.4: возвращаемое значение обязано по-прежнему
    различать «эпизод НОВЫЙ» и «эпизод уже шёл».

    От этого зависит событие `takeover` в журнале, и терять разницу нельзя —
    она защищает от трёх эпизодов на один залп сообщений. Реализация,
    заменившая условный `UPDATE` безусловным и вернувшая `True` всегда, эту
    защиту снимает молча.
    """
    t0 = 900.0
    store.mute(TG, source=WEAK_PAUSE_SOURCE, until=t0 + HOUR, now=t0)
    first = _human_sends_from_panel(store, TG, text="первое", now=t0 + 10.0)
    second = _human_sends_from_panel(store, TG, text="второе", now=t0 + 20.0)

    assert first is True, (
        "отправка человеком поверх СНУЗА не открыла новый эпизод (%r): это и "
        "есть съеденный перехват" % (first,))
    assert second is False, (
        "второе нажатие подряд объявлено НОВЫМ эпизодом (%r): залп из трёх "
        "сообщений даст три эпизода и три события `takeover` (§2.4)"
        % (second,))


def test_5a_bessrochnaya_pauza_komandoi_NE_priobretaet_sroka(store):
    """§5 п.5а — обратная половина того же пина.

    «Бессрочная пауза командой владельца срока не приобретает»: `pause_until`
    и так `NULL`, повышение источника её НЕ ОСЛАБЛЯЕТ. Ошибка здесь тише
    первой: реализация, поставившая `pause_until = now + что-нибудь` «чтобы не
    висело вечно», отменяет явную команду владельца таймером — а
    `should_auto_resume` прямо оговаривает, что таймер этого делать не вправе.
    """
    t0 = 900.0
    store.mute(TG, source=WEAK_PAUSE_SOURCE, until=None, now=t0)
    assert _contact(store, TG)["pause_until"] is None, "предпосылка: срока нет"

    _human_sends_from_panel(store, TG, text="я отвечу сам", now=t0 + 60.0)

    row = _contact(store, TG)
    assert row["pause_until"] is None, (
        "бессрочная пауза приобрела срок %r: явную команду владельца отменит "
        "таймер (§2.4, §5 п.5а)" % (row["pause_until"],))
    assert row["pause_source"] == STRONG_PAUSE_SOURCE, (
        "источник остался %r: повышение силы не произошло и на бессрочной "
        "паузе" % (row["pause_source"],))

    far = t0 + 30 * 24 * HOUR
    assert is_muted(row, kill_switch=False, now=far), (
        "через месяц бессрочная пауза считается снятой: срок всё-таки завёлся")
    spoke = _bot_turn(store, TG, reply="НЕ ДОЛЖНО УЙТИ ЛИДУ", clock=far)
    assert spoke.sent == [], (
        "бот заговорил в диалоге под бессрочной паузой владельца: %r"
        % (spoke.sent,))


# ═══ §5 п.6: ОТПРАВКА ПРОХОДИТ ПОВЕРХ ТУМБЛЕРОВ ═════════════════════════════

def test_tumblery_ne_meshayut_cheloveku_na_LYUBOM_kanale_i_glushat_bota(store):
    """§5 п.6, обе половины в ОДНОМ стороже — и это не стиль.

    Тумблеры выключают БОТА, а не человека: владелец, выключивший бота, обязан
    иметь возможность ответить клиенту сам ровно тогда, когда это нужнее всего.
    Обратная ошибка тише: «человек проходит» легко превратить в «проходит кто
    угодно».

    🔴 ОТЛИЧИЕ ОТ СМЕРЖЕННОГО СТОРОЖА `test_KILL_SWITCH_i_FUNNEL_GATE_…`
    (`tests/chatter/test_outgoing_queue.py`): тот меряет ТЕЛЕГРАМНЫЙ путь через
    `deliver_outgoing`. Здесь тот же контракт меряется на канале, которого в
    дереве нет, — иначе «поверх тумблеров» окажется свойством Telegram.
    """
    store.set_runtime_flag("kill_switch", "1", ts=900.0)
    store.mute(MOON, source=WEAK_PAUSE_SOURCE, until=900.0 + HOUR, now=900.0)

    verdict, deliverer = _through_core(
        store, MOON, "бот выключен, отвечаю я", token="tok-moon-toggles",
        msg_id="moon-3")

    assert verdict == "sent", (
        "человек не прошёл поверх тумблеров на канале `%s` (вердикт %r): "
        "владелец, выключивший бота, не может ответить клиенту сам"
        % (MOON_CHANNEL, verdict))
    assert deliverer.saw("бот выключен, отвечаю я") == 1, deliverer.calls

    spoke = _bot_turn(store, TG, reply="НЕ ДОЛЖНО УЙТИ ЛИДУ", clock=1000.0)
    assert spoke.sent == [], (
        "при взведённом `kill_switch` бот всё-таки заговорил: рубильник, "
        "который глушит не всех, — это рубильник, которому нельзя верить; %r"
        % (spoke.sent,))


# ═══ §5 п.7 и п.8: ДВОЙНОЕ НАЖАТИЕ И F5 ═════════════════════════════════════

@pytest.fixture()
def stand(tmp_path, monkeypatch):
    """Инстанс клиентской панели: своя БД, свой слаг, свой ключ.

    Живой `.secrets/` и живой Telegram не участвуют: путь к базе подменяется
    переменной окружения, как это делают остальные панельные сторожа дома.
    """
    def build():
        db = tmp_path / "panel.db"
        s = Store(str(db))
        for cid in (TG, FREE, MOON, WEB):
            s.get_or_create_contact(cid)
        s.close()

        beat = tmp_path / "state" / ("chatter_heartbeat_%s.txt" % SLUG)
        beat.parent.mkdir(parents=True, exist_ok=True)
        beat.write_text("beat", encoding="utf-8")

        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("JARVIS_PANELS_KEY", "web-d-key")
        monkeypatch.setenv("TAMAPI_DB", str(db))
        monkeypatch.setenv("TAMAPI_SLUG", SLUG)
        monkeypatch.setenv("CHATTER_CLIENTS_DIR", str(CLIENTS_DIR))
        monkeypatch.delenv("TAMAPI_HEARTBEAT", raising=False)

        import app.routers.panels_auth as pa
        import app.routers.tamapi_dashboard as td
        for m in (pa, td):
            importlib.reload(m)
        import app.panel_client as pc
        importlib.reload(pc)
        return pc.build_app(), str(db)
    return build


DIALOG_PATH_CANDIDATES = ("/panel/tamapi/d/%s", "/panel/d/%s", "/d/%s")
TOKEN_FIELD = "event_token"
_TOKEN_RE = re.compile(
    r'name=["\']%s["\'][^>]*value=["\']([^"\']+)["\']' % TOKEN_FIELD)
_TOKEN_RE_REV = re.compile(
    r'value=["\']([^"\']+)["\'][^>]*name=["\']%s["\']' % TOKEN_FIELD)


def _client(api):
    from fastapi.testclient import TestClient
    c = TestClient(api)
    c.cookies.set("panels_key", "web-d-key")
    return c


def _dialog(client, contact_id):
    """Страница диалога (§2.7, адрес `…/d/<contact_id>`). Возвращает ответ."""
    last = None
    for shape in DIALOG_PATH_CANDIDATES:
        last = client.get(shape % contact_id)
        if last.status_code != 404:
            return last
    return last


def _form_token(html: str) -> str:
    m = _TOKEN_RE.search(html) or _TOKEN_RE_REV.search(html)
    assert m, (
        "на странице диалога нет поля `%s` (§2.5): токен приходит снаружи и "
        "никем не порождается — значит правила, откуда он берётся, нет, и "
        "двойное нажатие защищать нечем" % TOKEN_FIELD)
    return m.group(1)


def _open_dialog_with_token(client, contact_id) -> str:
    r = _dialog(client, contact_id)
    assert r.status_code == 200, (
        "§2.7 %s: страницы диалога `…/d/<contact_id>` нет (HTTP %s). Без неё "
        "кнопке «отправить» негде находиться — ровно поэтому смерженная ручка "
        "`POST /api/outgoing` так и осталась без кнопки (§1.2 п.2)"
        % (_SPEC, r.status_code))
    return _form_token(r.text)


def test_dvoinoe_nazhatie_ne_zadvaivaet_zadanie_i_govorit_ob_etom(stand, store):
    """§5 п.7, первое направление: ТОТ ЖЕ токен → ровно одно сообщение и
    `duplicate: true`.

    Идемпотентность в базе есть (`token` UNIQUE + перечитывание на
    `IntegrityError`), но проверяется здесь ИСХОД, а не механика: сколько
    сообщений увидел бы лид. Реализация, забывшая гасить токен в форме, эту
    половину прошла бы — её ловит §5 п.8 ниже.
    """
    api, db = stand()
    c = _client(api)
    body = {"contact_id": MOON, "text": "одно и то же нажатие",
            TOKEN_FIELD: "tok-double"}
    first = c.post("/api/outgoing", data=body)
    second = c.post("/api/outgoing", data=body)

    assert first.status_code == 200, (first.status_code, first.text[:300])
    assert second.status_code == 200, (second.status_code, second.text[:300])
    assert first.json().get("duplicate") is False, first.json()
    assert second.json().get("duplicate") is True, (
        "повтор по тому же токену не назван повтором (%r): молчание здесь — "
        "это человек, жмущий третий раз (§2.5 п.3)" % (second.json(),))
    assert second.json().get("id") == first.json().get("id"), (
        "второе нажатие породило ВТОРОЕ задание: %r vs %r"
        % (first.json(), second.json()))

    s = Store(db)
    try:
        rows = [dict(r) for r in s._conn.execute(
            "SELECT * FROM outgoing_queue WHERE contact_id=?", (MOON,))]
    finally:
        s.close()
    assert len(rows) == 1, (
        "в очереди %d заданий на одно нажатие: лид получит два сообщения; %r"
        % (len(rows), rows))

    # 🔴 ОДНА СТРОКА В ОЧЕРЕДИ — ЭТО ЕЩЁ НЕ ИСХОД. Считать надо СКОЛЬКО
    # СООБЩЕНИЙ УВИДЕЛ БЫ ЛИД: идемпотентность базы (`token` UNIQUE) живёт с
    # 25.08 и без этой половины сторож зелен уже сегодня, то есть ничего не
    # стережёт ([[jarvis-checks-that-answer-the-wrong-question]]).
    s = Store(db)
    try:
        row = [r for r in s.pending_outgoing() if r["contact_id"] == MOON][0]
        deliverer = _Deliverer(msg_id="moon-dbl")
        undo = _register(MOON_CHANNEL, deliverer)
        try:
            _deliver(s, row, now=1000.0, personas=_personas(s))
        finally:
            undo()
        lenta = [m for m in s.history(MOON) if m["text"] == "одно и то же нажатие"]
    finally:
        s.close()
    assert deliverer.saw("одно и то же нажатие") == 1, (
        "два нажатия одной кнопкой довели до канала %d сообщени(й): лид "
        "прочитает своё «Добрый день» дважды; %r"
        % (deliverer.saw("одно и то же нажатие"), deliverer.calls))
    assert len(lenta) == 1, (
        "два нажатия оставили %d строк(и) в ленте: %r" % (len(lenta), lenta))


def test_DRUGOI_token_s_TEM_ZHE_tekstom_daet_DVA_soobsheniya(store):
    """§5 п.7, второе направление — и без него первое зелено у кода, который
    не отправляет НИЧЕГО.

    §2.5 п.1: токен — личность НАЖАТИЯ, а не текста. Два одинаковых «Добрый
    день» — два сообщения, и оба обязаны уйти.
    """
    v1, d1 = _through_core(store, MOON, "Добрый день", token="tok-a",
                           msg_id="moon-a")
    v2, d2 = _through_core(store, MOON, "Добрый день", token="tok-b",
                           msg_id="moon-b")
    assert (v1, v2) == ("sent", "sent"), (v1, v2)

    rows = _row(store, MOON)
    assert len(rows) == 2 and [r["status"] for r in rows] == ["sent", "sent"], (
        "два РАЗНЫХ нажатия с одинаковым текстом схлопнулись в одно (%r): "
        "«отправить то же самое ещё раз» стало невозможным навсегда (§2.5 п.1)"
        % (rows,))
    lenta = [m for m in store.history(MOON) if m["text"] == "Добрый день"]
    assert len(lenta) == 2, (
        "в ленте %d реплик вместо двух: %r" % (len(lenta), lenta))
    assert d1.saw("Добрый день") == 1 and d2.saw("Добрый день") == 1, (
        d1.calls, d2.calls)


def test_F5_posle_uspeha_ne_rozhdaet_vtorogo_zadaniya(stand):
    """§5 п.8. Форма отдаёт НОВЫЙ токен только после подтверждённой постановки.

    §2.5 п.2 дословно: успешная постановка гасит токен в форме и рождает
    следующий — иначе F5, «назад» и повторный POST браузера уедут ТЕМ ЖЕ
    ключом. Две половины: (а) повторный POST старым токеном второго задания не
    рождает; (б) свежий рендер страницы несёт ДРУГОЙ токен, иначе следующее
    сообщение владельца молча схлопнется с предыдущим — и это дефект в другую
    сторону, куда тише.
    """
    api, db = stand()
    c = _client(api)
    tok = _open_dialog_with_token(c, MOON)

    ok = c.post("/api/outgoing",
                data={"contact_id": MOON, "text": "первое", TOKEN_FIELD: tok})
    assert ok.status_code == 200 and ok.json().get("duplicate") is False, ok.text

    again = c.post("/api/outgoing",
                   data={"contact_id": MOON, "text": "первое", TOKEN_FIELD: tok})
    assert again.json().get("duplicate") is True, (
        "повторный POST браузера (F5) породил ВТОРОЕ задание: %r" % (again.json(),))

    fresh = _open_dialog_with_token(c, MOON)
    assert fresh != tok, (
        "после подтверждённой постановки форма отдаёт ТОТ ЖЕ токен %r: "
        "следующее сообщение владельца схлопнется с предыдущим и не уедет "
        "вовсе (§2.5 п.2)" % (tok,))


def test_generator_tokenov_SLUCHAEN_a_ne_schetchik_i_ne_vremya(stand):
    """§5 п.8, парная половина — §2.5 п.4 названо следствием вслух.

    Строка очереди НЕ УДАЛЯЕТСЯ НИКОГДА, значит и токен живёт вечно. Генератор
    обязан быть случайным: счётчик или время с секундной точностью через месяц
    молча «продублируют» чужое задание — и панель ответит `duplicate: true` на
    сообщение, которого владелец не отправлял.
    """
    api, _db = stand()
    c = _client(api)
    tokens = [_open_dialog_with_token(c, MOON) for _ in range(12)]

    assert len(set(tokens)) == len(tokens), (
        "рендеры страницы отдают повторяющиеся токены: %r" % (tokens,))
    assert all(len(t) >= 12 for t in tokens), (
        "токены слишком короткие для случайных (%r): коллизия на вечно живущей "
        "таблице — вопрос времени" % (tokens,))
    numeric = [t for t in tokens if t.strip().isdigit()]
    assert len(numeric) < len(tokens), (
        "все токены — числа (%r): счётчик или время с секундной точностью, а "
        "не случайность (§2.5 п.4)" % (tokens,))


# ═══ §5 п.9: КАНАЛ БЕЗ ДОСТАВЩИКА — ОТКАЗ СЛОВАМИ, ИЗ ОДНОЙ ФУНКЦИИ ═════════

def test_kanal_bez_dostavshika_otkazyvaet_SLOVAMI_i_ne_visit_pending(store):
    """§5 п.9. `deliverer_for("web")` в паре D НЕ регистрируется (§2.6), и
    задание обязано получить ГРОМКИЙ отказ — тем же путём, что уже работает
    для клиента без Telegram.

    Вечно `pending` — это тишина с возрастом: владелец считает сообщение
    отправленным, а его не заберёт никто.
    """
    _enqueue(store, WEB, "в веб-диалог", token="tok-web-1")
    row = [r for r in store.pending_outgoing() if r["contact_id"] == WEB][0]
    verdict = _deliver(store, row, now=1000.0, personas=_personas(store))

    assert verdict == "refused", (
        "задание в канал без доставщика дало вердикт %r: `retry` означает "
        "повтор каждые 5 секунд до конца времён, `sent` — потерянное "
        "сообщение" % (verdict,))
    got = _row(store, WEB)[0]
    assert got["status"] == "refused", (
        "строка осталась в статусе %r — задание висит `pending` вечно"
        % (got["status"],))
    assert got["last_error"] and any(ch.isalpha() for ch in str(got["last_error"])), (
        "отказ не объяснён СЛОВАМИ (last_error=%r): владелец увидит «не "
        "доставлено» и пойдёт гадать" % (got["last_error"],))
    assert store.history(WEB) == [], (
        "неотправленное попало в историю: %r" % (store.history(WEB),))


def test_can_send_now_otvechaet_ZARANEE_toi_zhe_prichinoi(store):
    """§5 п.9, вторая половина: та же причина приходит из `can_send_now` ДО
    показа поля ввода.

    Fail-closed на доставке — это ПОЗДНО: человек уже набрал текст.
    """
    can_send_now = _core_attr(
        "can_send_now",
        "§2.6 называет её одной функцией на двух вызывателей: панель зовёт ДО "
        "показа поля ввода, ядро — ПЕРЕД отправкой")

    ahead = can_send_now(WEB)
    assert ahead is not None, (
        "`can_send_now` разрешает писать в канал, у которого доставщика нет: "
        "человек наберёт текст и узнает об отказе только после отправки")
    assert getattr(ahead, "human", None), (
        "отказ из `can_send_now` безымянен (%r): §2.6 требует сказать «в этот "
        "канал написать нельзя, ПОТОМУ ЧТО …»" % (ahead,))

    assert can_send_now(MOON) is None or getattr(can_send_now(MOON), "reason", None), (
        "`can_send_now` отвечает мусором на канал с доставщиком")

    _enqueue(store, WEB, "в веб-диалог", token="tok-web-2")
    row = [r for r in store.pending_outgoing() if r["contact_id"] == WEB][0]
    _deliver(store, row, now=1000.0, personas=_personas(store))
    got = _row(store, WEB)[0]
    assert str(ahead.human) in str(got["last_error"]), (
        "панель говорит одно (%r), доставка — другое (%r): два ответа на один "
        "вопрос разъедутся, и меньший погасит больший молча "
        "([[jarvis-two-numbers-for-one-thing]])"
        % (ahead.human, got["last_error"]))


def test_podmena_can_send_now_menyaet_OBA_otveta(stand, monkeypatch, store):
    """§5 п.9, ПИН: обе стороны берут причину из ОДНОЙ функции.

    Доказывается ПОДМЕНОЙ, а не совпадением текстов сегодня: совпадение рвётся
    в день изменения и слепнет зелёным
    ([[jarvis-checks-that-answer-the-wrong-question]]). Подменяю функцию —
    обязаны измениться ОБА ответа: и предупреждение на странице, и слова
    отказа в строке очереди.
    """
    mod = _core()
    Refusal = getattr(tr, "Refusal")
    marker = "ЛУНА СЕГОДНЯ НЕ ПРИНИМАЕТ"

    def fake(contact_id, *a, **k):
        return Refusal("channel_window_closed", marker)

    monkeypatch.setattr(mod, "can_send_now", fake)

    api, db = stand()
    c = _client(api)
    page = _dialog(c, MOON)
    assert page.status_code == 200, (page.status_code, page.text[:300])
    assert marker in page.text, (
        "подменённая `can_send_now` не изменила предупреждение на странице "
        "диалога: панель считает возможность отправки ВТОРОЙ реализацией "
        "(§2.6)")

    _enqueue(store, MOON, "текст", token="tok-moon-subst")
    row = [r for r in store.pending_outgoing() if r["contact_id"] == MOON][0]
    _deliver(store, row, now=1000.0, personas=_personas(store))
    assert marker in str(_row(store, MOON)[0]["last_error"]), (
        "подменённая `can_send_now` не изменила отказ доставки: ядро считает "
        "возможность отправки СВОЕЙ реализацией (§2.6)")


# ═══ §5 п.10: СЛИВ ОЧЕРЕДИ НЕ ЗАДВАИВАЕТСЯ ══════════════════════════════════

def test_dva_processa_s_raznymi_dostavshikami_ne_zadvaivayut_otpravku(store):
    """§5 п.10. Два процесса с РАЗНЫМИ доставщиками над одной базой.

    §2.8: сливает тот процесс, который ВЛАДЕЕТ каналом; таблица делится по
    каналу. Альтернатива «кто первый взял» требует аренды строки и блокировки —
    это второй механизм на ту же вещь.

    Проверяется свойством, а не реализацией: задание видит ровно один процесс,
    и уходит оно один раз.
    """
    _enqueue(store, MOON, "ровно одному процессу", token="tok-moon-split")

    mine = _pending_for(store, (MOON_CHANNEL,))
    theirs = _pending_for(store, REGISTERED_CHANNELS_TODAY)

    assert [r["contact_id"] for r in mine] == [MOON], (
        "процесс, ВЛАДЕЮЩИЙ каналом `%s`, не видит своё задание: %r"
        % (MOON_CHANNEL, mine))
    assert [r for r in theirs if r["contact_id"] == MOON] == [], (
        "процесс без доставщика `%s` видит чужое задание (%r): два процесса, "
        "читающих `pending` без блокировки, отправят его ДВАЖДЫ (§2.8)"
        % (MOON_CHANNEL, theirs))


def test_nikem_ne_zabrannoe_zadanie_ostayotsya_VIDIMYM_lampe(store, tmp_path):
    """§5 п.10, вторая половина — и без неё фильтр по каналу опаснее болезни.

    §2.8 дословно: лампа `stuck` продолжает считать задание по возрасту, то
    есть НИКЕМ НЕ ЗАБРАННОЕ задание остаётся видимым, а не исчезает из обеих
    выборок. Реализация, отфильтровавшая канал заодно и в лампе, превращает
    потерянное сообщение в тишину без возраста.
    """
    from app.panel_client import OUTGOING_STUCK_AFTER, outgoing_summary
    from app.services.tamapi_metrics import outgoing_raw

    _enqueue(store, WEB, "никем не забрано", token="tok-web-stuck", now=0.0)

    raw = outgoing_raw(str(tmp_path / "web_d.db"))
    late = OUTGOING_STUCK_AFTER + 60.0
    summary = outgoing_summary(raw, now=late)

    assert summary["pending"] >= 1, (
        "задание в канал, которого этот процесс не обслуживает, пропало из "
        "лампы (%r): потерянное сообщение стало тишиной без возраста (§2.8)"
        % (summary,))
    assert summary["stuck"] is True, (
        "лампа `stuck` молчит на задании возрастом %.0f с при пороге %.0f: %r"
        % (late, OUTGOING_STUCK_AFTER, summary))


# ═══ §5 п.11: ПОРОГ stuck ОСТАЁТСЯ ОДИН ═════════════════════════════════════

_STUCK_SCAN_DIRS = ("app", "chatter", "scripts")


def test_porog_stuck_na_dereve_ROVNO_ODIN():
    """§5 п.11. Пинится ОТСУТСТВИЕ второго числа, а не равенство значений.

    «Значения равны» зелено ровно до дня, когда одно из двух поправят, — и
    меньшее погасит большее молча ([[jarvis-two-numbers-for-one-thing]]).
    Порог у очереди один и после появления второго канала (§3).

    ⚠️ Регрессионный пин: сегодня он ЗЕЛЁН (одно присваивание,
    `app/panel_client.py`). Он и обязан оставаться зелёным — краснеть ему
    в тот день, когда веб-доставке заведут «свои десять минут».
    """
    found = []
    for d in _STUCK_SCAN_DIRS:
        for path in sorted((REPO_ROOT / d).rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in tree.body:
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    if isinstance(t, ast.Name) and "STUCK" in t.id and "OUTGOING" in t.id:
                        found.append("%s:%s=%s" % (path.name, t.id,
                                                   ast.unparse(node.value)))
    assert len(found) == 1, (
        "порогов «застряло» на дереве %d вместо одного: %r. Два числа на одну "
        "вещь разъезжаются молча, и меньшее гасит большее (§3, §5 п.11)"
        % (len(found), found))


def test_lampa_edet_za_konstantoi_a_ne_za_svoim_chislom():
    """§5 п.11, парная половина: свёртка следует за КОНСТАНТОЙ.

    Доказывается СДВИГОМ константы, а не совпадением значений сегодня: иначе
    сторож проверяет число, а не связь.
    """
    import app.panel_client as pc
    raw = {"pending": 1, "refused": 0, "oldest_created_ts": 0.0}
    at = pc.OUTGOING_STUCK_AFTER + 1.0
    assert pc.outgoing_summary(raw, now=at)["stuck"] is True, "предпосылка"
    assert pc.outgoing_summary(raw, now=pc.OUTGOING_STUCK_AFTER - 1.0)["stuck"] is False, (
        "лампа горит ДО порога: у неё своё число")


# ═══ §5 п.12: НИ ОДНОГО `Store(` ВНЕ `with` ═════════════════════════════════

def _store_calls_outside_with(path: Path) -> list[str]:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    inside = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                inside.add(id(item.context_expr))
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else (
            fn.attr if isinstance(fn, ast.Attribute) else None)
        if name != "Store" or id(node) in inside:
            continue
        bad.append("%s:%d" % (path.name, node.lineno))
    return bad


@pytest.mark.parametrize("src", [PANEL_CLIENT_SRC, DASHBOARD_SRC],
                         ids=lambda p: p.name)
def test_ni_odnogo_Store_vne_with(src):
    """§5 п.12, включая НОВУЮ страницу диалога и её ручку.

    §1.5 снял утверждение «`/panel/tamapi/action` течёт хэндлом БД» ФАКТОМ —
    DEV-48 смержена, вся ручка обёрнута `with`. Из снятого утверждения остаётся
    ОБЯЗАТЕЛЬСТВО: новая страница диалога и новая ручка наследуют ту же идиому,
    и это предмет сторожа, а не аккуратности. Панель живёт неделями, и нажатие
    кнопки не имеет права оставлять открытый хэндл файла БД: на Windows такой
    хэндл держит файл занятым, и бэкап начинает падать.

    Пин по AST, а не по строке: `del`-ы и комментарии не считаются.

    ⚠️ Регрессионный пин: сегодня ЗЕЛЁН.
    """
    bad = _store_calls_outside_with(src)
    assert bad == [], (
        "`Store(...)` вне `with` в %s: %r — процесс, живущий неделями, "
        "оставляет за собой открытый хэндл файла БД (§5 п.12)"
        % (src.name, bad))


# ═══ §5 п.13: СТРАНИЦА ДИАЛОГА FAIL-CLOSED ПО АДРЕСУ ════════════════════════

def test_stranica_dialoga_pokazyvaet_SVOI_dialog(stand):
    """ПРЕДПОСЫЛКА сторожа п.13 — и без неё он зелен по построению.

    Отказ на чужой адрес неотличим от отказа на ЛЮБОЙ адрес, пока страницы нет
    вовсе: 404 — тоже отказ. Поэтому сначала доказывается, что своя лента
    показывается ([[jarvis-absence-is-not-contradiction]]).
    """
    api, db = stand()
    s = Store(db)
    try:
        s.add_message(TG, "user", "вопрос лида", ts=1000.0)
        s.add_message(TG, "assistant", "ответ бота", ts=1001.0)
    finally:
        s.close()

    c = _client(api)
    r = _dialog(c, TG)
    assert r.status_code == 200, (
        "§2.7 %s: страницы диалога нет (HTTP %s) — кнопке «отправить» негде "
        "находиться" % (_SPEC, r.status_code))
    assert "вопрос лида" in r.text and "ответ бота" in r.text, (
        "лента своего диалога пуста: показывать нечего, и «чужое не "
        "показываем» доказывать не на чем")


@pytest.mark.parametrize("alien, why", [
    ("telegram:111:foreign", "чужой слаг: диалог другого клиента"),
    ("telegram:999:demo", "контакта нет в базе этого клиента"),
    ("../../etc/passwd", "адрес вообще не contact_id"),
    # Четвёртый вход добавлен мержем пары C: сегодняшняя двухсегментная форма
    # после миграции — тоже ЧУЖОЙ адрес, и отказ на ней обязан быть таким же
    # молчаливым для лида и таким же громким в логе. Без этой строки арка
    # «форма сменилась» опиралась бы на то, что старую форму никто не введёт.
    ("111:demo", "двухсегментная форма: адрес до миграции, после неё невалиден"),
])
def test_stranica_dialoga_FAIL_CLOSED_po_adresu(stand, alien, why):
    """§5 п.13. Чужой `contact_id` → ОТКАЗ, а не пустая лента.

    Цена ошибки — показ чужой переписки, и она та же, что в §3.1 спеки веба.
    Пустая лента здесь хуже отказа вдвойне: она выглядит как «диалог пуст», то
    есть врёт молча и приглашает написать в него из формы.

    🔴 ПРЕДПОСЫЛКА ВНУТРИ, А НЕ СОСЕДНИМ ТЕСТОМ. Пока страницы нет вовсе, GET
    даёт 404 — то есть «отказ», и сторож ЗЕЛЁН ПО ОТСУТСТВИЮ. Третий вердикт
    («не состоялось»), выставленный выше второго, поменял бы смысл всего
    прогона ([[jarvis-blocked-verdict-swallows-the-red]]), поэтому «своя лента
    показывается» проверяется здесь же и первой строкой.
    """
    api, db = stand()
    s = Store(db)
    try:
        s.add_message(TG, "user", "вопрос лида", ts=1000.0)
    finally:
        s.close()
    c = _client(api)
    own = _dialog(c, TG)
    assert own.status_code == 200 and "вопрос лида" in own.text, (
        "предпосылка: своя лента обязана показываться (HTTP %s). Без неё "
        "отказ на чужой адрес неотличим от отсутствия страницы, и сторож "
        "зелен по построению" % (own.status_code,))

    r = _dialog(c, alien)
    assert r.status_code >= 400, (
        "%s: страница ответила HTTP %s вместо отказа — показ чужой переписки "
        "стоит ровно столько же, сколько в §3.1 спеки веба"
        % (why, r.status_code))
    assert TOKEN_FIELD not in r.text, (
        "%s: на отказе всё равно отдано поле ввода — форма приглашает писать "
        "в чужой диалог" % why)


# ═══ §5 п.14: brain НЕ ОТДАЁТ author В API ══════════════════════════════════

def test_author_ne_uezzhaet_v_API_i_posle_pary_D():
    """§5 п.14. Пин от арки 25.08 обязан ОСТАТЬСЯ зелёным.

    Пара D добавляет второй канал и второго автора записей в ленте; состав
    полей вызова Anthropic от этого меняться не имеет права. Роли `'human'` в
    том API нет, а лишний ключ в элементе — отказ вызова.

    ⚠️ Регрессионный пин: сегодня ЗЕЛЁН намеренно (спека §5 п.14 дословно —
    «обязан остаться зелёным»). Дублирует по смыслу
    `tests/chatter/test_message_author.py::test_v_api_uezzhaet_TOLKO_rol_i_tekst`
    и стоит здесь потому, что радиус правки пары D его затрагивает.
    """
    out = build_messages([
        {"role": "user", "text": "вопрос", "ts": 1.0, "author": "bot"},
        {"role": "assistant", "text": "из панели", "ts": 2.0, "author": "human"},
    ])
    assert out == [{"role": "user", "content": "вопрос"},
                   {"role": "assistant", "content": "из панели"}], out
    for item in out:
        assert set(item) == {"role", "content"}, (
            "в вызов Anthropic уехал лишний ключ %r: вызов будет отвергнут"
            % (sorted(set(item) - {"role", "content"}),))


# ═══ §5 п.15: ССЫЛКА «ОТКРЫТЬ ДИАЛОГ» НЕ СТРОИТСЯ КАК t.me ══════════════════

_TELEGRAM_URL_MARKERS = ("t.me", "tg://", "telegram.me")


def test_ssylka_otkryt_dialog_dlya_NEtelegramnogo_kontakta_ne_telegramnaya(store):
    """§5 п.15. Сторож краснеет ДАЖЕ пока правка `console.py` отложена (§2.7)
    — он и есть тот сигнал, что отсрочка кончилась.

    §2.7 обосновывает отсрочку тем, что сегодня ссылка врёт ТОЛЬКО для
    не-телеграмных контактов, а их в базах не существует до волны 3. Это
    условие, а не «потом»: в день появления первого `web:`-контакта отсрочка
    перестаёт быть безопасной. Сторож стоит на этом дне.

    ⚠️ Поправка к букве спеки: спека называет `t.me`, а живой `contact_link`
    без юзернейма отдаёт `tg://user?id=…`. Проверяется всё телеграмное
    семейство — иначе сторож зелен на сегодняшнем коде, то есть бесполезен.
    """
    store.get_or_create_contact(MOON)
    res = route_callback("open:%s" % MOON, store=store, now=1000.0,
                         language="ru", snooze_seconds=HOUR,
                         event_token="tok-open")
    text = "%s %s" % (res.feedback_html, res.answer)
    guilty = [m for m in _TELEGRAM_URL_MARKERS if m in text]
    assert not guilty, (
        "ссылка «открыть диалог» для контакта `%s` собрана как телеграмная "
        "(%r): владелец нажмёт и попадёт в никуда, а разбираться будет с "
        "Telegram; %r" % (MOON, guilty, text[:200]))
    assert Action.OPEN is not None  # ветка OPEN — предмет §5 п.15


def test_ssylka_dlya_telegramnogo_kontakta_ne_slomana(store):
    """ПАРНАЯ половина п.15: регресса нет.

    Без неё сторож выше зелёный у реализации, выкинувшей ссылку вообще, — а
    это отняло бы у владельца работающую сегодня навигацию.
    """
    res = route_callback("open:%s" % TG, store=store, now=1000.0,
                         language="ru", snooze_seconds=HOUR,
                         event_token="tok-open-tg")
    text = "%s %s" % (res.feedback_html, res.answer)
    assert any(m in text for m in _TELEGRAM_URL_MARKERS), (
        "телеграмный контакт потерял телеграмную ссылку: %r" % (text[:200],))


# ═══ ПРЕДПОСЫЛКИ СТЕНДА ═════════════════════════════════════════════════════

def test_kontrakt_Transport_NE_rasshiryaetsya_shestym_metodom():
    """§1.3 + §2.2. `Transport` объявляет РОВНО 5 abstract-методов, и пара D их
    число не меняет.

    `send_returning_id` в контракт не входит: он есть только у
    `TelethonTransport`, а `FakeConsoleTransport` про него не знает. ABC
    реализуют три класса, и расширение ради одного потребителя ломает два
    других. Доставщик отдаёт `SentRef` (§2.2) — вот почему шестого метода не
    появляется.
    """
    from chatter.transport.base import Transport
    names = sorted(Transport.__abstractmethods__)
    assert names == ["read_acknowledge", "receive", "send", "send_typing",
                     "set_online"], (
        "состав abstract-методов `Transport` изменился: %r. §2.2 запрещает "
        "расширять контракт ради доставки — это ломает `FakeConsoleTransport` "
        "и консольный раннер" % (names,))
    assert "send_returning_id" not in names, (
        "`send_returning_id` въехал в контракт `Transport`: первый же "
        "`WebTransport`, честно реализовавший пять методов спеки веба §5, "
        "свалится на шестом (§1.3)")


def test_stend_deistvitelno_gonyaet_NEtelegramnyi_kanal(store):
    """Предпосылка всего файла: контакт `%s` ДЕЙСТВИТЕЛЬНО не телеграмный.

    Без неё все сценарии выше могли бы незаметно ездить телеграмным путём.

    🔴 ПРОВЕРКА ПЕРЕПИСАНА МЕРЖЕМ ПАРЫ C (29.08), и вот почему — это важнее
    самой правки. Раньше предпосылка звучала как «`contact_ref` этот адрес НЕ
    РАЗБИРАЕТ»: до пары C он знал только двухсегментную телеграмную форму, и
    неразбираемость была синонимом «другого канала». После пары C разбор стал
    канало-независимым — он разбирает ЛЮБОЙ канал из реестра, и старая
    формулировка требовала бы, чтобы владелец разбора чего-то НЕ умел. Сторож,
    построенный на неумении, зеленеет ровно до того дня, когда неумение чинят.

    Предпосылка та же, механизм другой: спрашиваем не «разбирается ли», а
    «какой это канал» — и отдельно, что ТЕЛЕГРАМНАЯ дверь (`telegram_peer_of`)
    этот адрес не пускает. Второе и есть то, ради чего писалась проверка:
    телеграмным путём такой контакт не поедет.
    """ % MOON
    from chatter.core.contact_ref import (
        TELEGRAM_CHANNEL, ContactRefError, channel_of, peer_of, telegram_peer_of)

    assert channel_of(MOON) == MOON_CHANNEL, (
        "канал стенда — не `%s`: сценарии выше не доказывают "
        "канало-независимость" % MOON_CHANNEL)
    assert channel_of(MOON) != TELEGRAM_CHANNEL, (
        "стенд оказался телеграмным: весь файл проверяет тот самый путь, "
        "мимо которого он написан")
    with pytest.raises(ContactRefError):
        # Телеграмная дверь обязана ОТКАЗАТЬ: адрес чужого канала в неё не
        # проходит, иначе доставщик Telegram однажды получил бы moonmail-лида.
        telegram_peer_of(MOON)

    assert peer_of(MOON) == "ext-77", (
        "собеседник чужого канала не читается: разбор перестал быть "
        "канало-независимым")
    assert peer_of(TG) == "111", "предпосылка: телеграмный контакт разбирается"
    assert telegram_peer_of(TG) == 111, (
        "телеграмная дверь перестала пускать телеграмный адрес")
