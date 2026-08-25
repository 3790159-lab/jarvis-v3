# -*- coding: utf-8 -*-
"""Сторожа арки «панель учится отправлять»: очередь, перехват, доставка.

Спека `docs/superpowers/specs/2026-08-25-panel-sends.md` (§2, §3, §7, §8, §10)
и `CONTRACT_panel_sends.md` (§1–§6).

СТОРОЖА ПИСАНЫ ОТ СПЕКИ И КОНТРАКТА. Реализацию писал другой автор в другом
дереве; его кода автор этих сторожей не видел и не искал
([[jarvis-guards-not-by-the-plan-author]]): сторож, написанный по коду, согласен
с кодом по определению и молчит ровно там, где код ошибся.

ПОЧЕМУ ЭТИ СТОРОЖА ВООБЩЕ НУЖНЫ — четыре аварии, все тихие:

1. ДВОЙНАЯ ОТПРАВКА. У панели есть кнопка, у кнопки есть двойное нажатие, а у
   доставки — поллинг. Без идемпотентности по токену лид получит одно и то же
   слово владельца дважды, и заметит это лид, а не мы.
2. БОТ ПОВЕРХ ЧЕЛОВЕКА. Сегодня «сообщение не наше» и «человек вмешался» —
   ОДНО событие; панель их разводит. Зарегистрировали своё исходящее и
   промолчали — перехват не встал, и бот продолжил сочинять поверх владельца.
   Это класс [[jarvis-catchup-reanswers-escalated-dialog]], и он тише двойной
   отправки, а значит дороже.
3. ЛИШНЯЯ КАРТОЧКА. Обратная ветка той же развилки: не зарегистрировали —
   раннер увидел СВОЁ исходящее как чужое и поднял карточку «человек
   вмешался» на нажатие самого владельца. Владелец учится жать «да» не глядя
   ([[jarvis-ask-bridge-auto-allow-suspect]]), и следующий вопрос, который
   правда требовал внимания, тоже будет прожат не глядя.
4. МОЛЧАЛИВЫЙ ОТКАЗ. «Не доставлено» без причины отправляет владельца гадать,
   а «доставлено» на самом деле недоставленного — врать клиенту его же руками.

ЧЕГО ЗДЕСЬ НЕТ: ни сети, ни живого `.secrets/`, ни живого Telegram. База — в
`tmp_path`, транспорт — двойник, Telethon-клиент — мок, чей `send_message`
и есть ЕДИНСТВЕННАЯ воронка отправки в этом дереве (и прямой путь, и
`TelethonTransport.send` упираются в него).

ИМЕНА КОНТРАКТА берутся через `getattr` в момент вызова, а не импортом на
уровне модуля: пока реализации нет, импорт отсутствующего имени сорвал бы
СБОР всего файла, и вместо двух десятков честно красных сторожей была бы одна
ошибка коллекции — то есть ноль измеренных утверждений.
"""
from __future__ import annotations

import asyncio
import ast
import dataclasses
import random
from pathlib import Path
import pytest

import chatter.telethon_run as tr
from chatter.config.loader import load_config
from chatter.core.admission import admission_decision
from chatter.core.brain import Brain
from chatter.core.console import Command
from chatter.core.llm import FakeLLM
from chatter.notify.base import Action
from chatter.notify.control_bot import route_callback
from chatter.run import Deps, process_batch
from chatter.storage.db import Store
from chatter.telethon_run import PersonaBundle, TelethonRunner, decide_outgoing
from chatter.transport.fake import FakeConsoleTransport
from chatter.transport.telethon_tg import SentRegistry

REPO_ROOT = Path(__file__).resolve().parents[2]
CLIENTS_DIR = REPO_ROOT / "chatter" / "clients"
RUNNER_SRC = REPO_ROOT / "chatter" / "telethon_run.py"

# ДВА контакта во всех сценариях, где реализация может оказаться верной для
# одного и слепой для второго (очередь, перехват, тумблеры, снятие паузы).
A = "111:demo"
B = "222:demo"

# §4 контракта: литеральный набор причин отказа. Выведенный из кода список
# согласен с кодом по определению ([[jarvis-literal-lists-not-introspection]]).
REFUSAL_REASONS = ("client_disabled", "no_transport",
                   "channel_window_closed", "unknown_contact")

DAY = 86400.0


# ── общая оснастка ──────────────────────────────────────────────────────────

def _need(name: str):
    """Достать имя контракта или упасть ВСЛУХ и с адресом договора.

    `AttributeError` из глубины теста назвал бы строку теста, а не строку
    контракта, которую забыли выполнить."""
    obj = getattr(tr, name, None)
    assert obj is not None, (
        "в `chatter/telethon_run.py` нет `%s` — имя названо контрактом "
        "дословно, и без него у панели нет пути к отправке" % name)
    return obj


class _Msg:
    """Ответ Telethon на `send_message`: у него есть `id`, и строкой он НЕ
    является — контракт §1 требует класть в очередь `sent_msg_id` СТРОКОЙ, а у
    веба и бизнес-API числовых id не будет вовсе."""

    def __init__(self, msg_id: int):
        self.id = msg_id


class _TypingCtx:
    """Асинхронный контекстный менеджер `client.action(...)`."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False


class _Handle:
    """Расписка пульта о ДОСТАВЛЕННОЙ карточке.

    Двойник отвечает успехом намеренно: сторож «карточки нет» обязан быть
    красным на реализации, которая карточку шлёт, а не зелёным на пульте,
    который её уронил."""

    ref = "saved:4242"


class _NotifierDouble:
    """Пульт, который ЗАПОМИНАЕТ карточки вместо отправки.

    Явный класс, а не `MagicMock`: у автомока `notify` существует всегда и
    истинен всегда, поэтому «карточка не ушла» на нём доказать нечем."""

    def __init__(self):
        self.cards: list = []

    def notify(self, card):
        self.cards.append(card)
        return _Handle()


class Sends:
    """Двойник `client.send_message` — единственной воронки отправки.

    Считает не «сколько раз позвали транспорт», а СКОЛЬКО СООБЩЕНИЙ УВИДЕЛ БЫ
    ЛИД: карточки владельцу уходят тем же вызовом с адресатом `"me"`, и
    смешать их с репликой лиду значит перестать различать ровно те две вещи,
    которые разводит §9.2 решения владельца.

    `state_at_send` — снимок строки контакта В МОМЕНТ вызова. Это и есть
    измерение ПОРЯДКА (§3 контракта): «перехват открыт до отправки» нельзя
    проверить по состоянию ПОСЛЕ — после верны оба порядка.
    """

    def __init__(self, store: Store, first_id: int = 9000):
        self.store = store
        self.calls: list[tuple] = []
        self.state_at_send: list[dict] = []
        self.next_id = first_id
        self.raises: BaseException | None = None

    async def __call__(self, chat, text=None, *args, **kwargs):
        self.calls.append((chat, text))
        if chat != "me":
            contact_id = _contact_for_chat(chat)
            if contact_id is not None:
                self.state_at_send.append(dict(self.store.get_or_create_contact(contact_id)))
            if self.raises is not None:
                raise self.raises
        self.next_id += 1
        return _Msg(self.next_id)

    def to_lead(self, text: str | None = None) -> list[tuple]:
        """Сообщения, которые увидел ЛИД (адресат не `"me"`)."""
        return [c for c in self.calls
                if c[0] != "me" and (text is None or c[1] == text)]

    def cards(self) -> list[tuple]:
        return [c for c in self.calls if c[0] == "me"]


class _EventDouble:
    """Входящее событие Telethon — ЯВНЫМИ значениями, а не автомоком.

    `chat = None` намеренно: `display_name` тогда честно падает на id, тогда
    как `MagicMock` подсунул бы туда истинный объект и имя лида собралось бы
    из мусора."""

    def __init__(self, *, raw_text: str, msg_id: int, chat_id: int):
        self.raw_text = raw_text
        self.message = _Msg(msg_id)
        self.chat_id = chat_id
        self.chat = None


class _ClientDouble:
    """Явный двойник Telethon-клиента — НИ ОДНОГО автомока.

    🔴 ПОВОД НАЗВАН ВСЛУХ, потому что на нём уже обожглись 25.08: `MagicMock`
    ИСТИНЕН и отвечает «да» на любой вопрос
    ([[jarvis-magicmock-truthy-spins-the-loop]]). Дубль на автомоке гасит
    РОВНО те ветки, ради которых сторож писался: `bundle is None` и
    `settings.telegram is None` не срабатывают никогда, все четыре отказа
    становятся недостижимы, а `await client.get_input_entity(...)` падает
    `TypeError: object can't be awaited` — то есть сторожа краснеют по вине
    сторожей, а не кода.

    Интерфейс — ровно тот, который спрашивает `deliver_outgoing` (дополнение
    к контракту §3): `send_message`, АСИНХРОННЫЙ `get_input_entity`, `action`.
    """

    def __init__(self, sends: Sends):
        self.send_message = sends
        self.entity_calls: list = []

    async def get_input_entity(self, peer_id):
        self.entity_calls.append(peer_id)
        return "entity:%s" % peer_id

    def action(self, *_a, **_k):
        return _TypingCtx()


# `peer_id -> contact_id`: чем именно адресована отправка, знает только
# реализация (голым peer, InputPeer'ом, строкой) — контракт называет лишь
# `get_input_entity(peer_id)`. Поэтому адресат восстанавливается по peer'у,
# который двойник клиента сам же и выдал.
_PEER_TO_CONTACT: dict[str, str] = {}


def _contact_for_chat(chat) -> str | None:
    """Какому контакту адресована отправка. None = разобрать нечем."""
    peer = str(chat)
    if peer.startswith("entity:"):
        peer = peer[len("entity:"):]
    peer = peer.split(":")[0]
    return _PEER_TO_CONTACT.get(peer)


def _persona(slug: str, store: Store, scripted=None) -> PersonaBundle:
    cfg = load_config(CLIENTS_DIR, slug)
    deps = Deps(cfg=cfg, store=store, brain=Brain(FakeLLM(scripted=scripted), cfg),
                rng=random.Random(0), clock=lambda: 1000.0, sleep=lambda _s: None)
    return PersonaBundle(cfg=cfg, deps=deps)


def _personas(store: Store, scripted=None) -> dict[str, PersonaBundle]:
    """Персоны стенда, и каждая нужна для СВОЕЙ ветки решения.

    * `demo` — здоровый клиент: канал подключён, отправка обязана пройти;
    * `mute` — тот же клиент с `settings.telegram = None`, то есть «канал не
      подключён» (исход `no_transport`);
    * слага `ghost` здесь НЕТ намеренно: `personas.get("ghost") is None` — это
      «клиент выключен в ростере либо живёт в другом процессе» (исход
      `client_disabled`). Отсутствие ключа и есть предмет договора, поэтому
      оно объявлено вслух, а не получается случайно.
    """
    demo = _persona("demo", store, scripted)
    assert demo.cfg.settings.telegram is not None, (
        "предпосылка стенда: у здоровой персоны канал ПОДКЛЮЧЁН, иначе "
        "«отправка прошла» проверять не на чем")
    muted = dataclasses.replace(
        demo.cfg, settings=dataclasses.replace(demo.cfg.settings, telegram=None))
    return {"demo": demo, "mute": PersonaBundle(cfg=muted, deps=demo.deps)}


def _runner(store: Store, *, funnel_gate: bool = False, scripted=None):
    """Настоящий `TelethonRunner` на временной базе и ЯВНЫХ двойниках.

    Настоящий раннер, а не самодельный объект с тремя атрибутами: двойник,
    у которого есть ровно то, что я угадал, молча разрешил бы реализации
    опираться на что угодно ещё. А вот `client` и `notifier` — рукописные
    классы с ЯВНЫМИ значениями: см. `_ClientDouble` о том, чем кончается
    автомок.
    """
    sends = Sends(store)
    client = _ClientDouble(sends)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
    runner = TelethonRunner(
        client=client, personas=_personas(store, scripted),
        primary_slug="demo", allowlist=frozenset({111, 222}), loop=loop,
        funnel_gate=funnel_gate)
    notifier = _NotifierDouble()
    runner.notifier = notifier
    return runner, sends, notifier


@pytest.fixture()
def store(tmp_path):
    """База — файл в `tmp_path`. Живой `.secrets/` не трогается никогда."""
    _PEER_TO_CONTACT.clear()
    s = Store(str(tmp_path / "outgoing.db"))
    for cid in (A, B):
        s.get_or_create_contact(cid)
        _PEER_TO_CONTACT[cid.split(":")[0]] = cid
    yield s
    s.close()


def _enqueue(store: Store, contact_id: str, text: str, *, token: str,
             now: float = 1000.0):
    _PEER_TO_CONTACT[contact_id.split(":")[0]] = contact_id
    return store.enqueue_outgoing(contact_id, text, token=token, now=now)


def _run(coro):
    return asyncio.run(coro)


# ═══ §1 контракта: ХРАНИЛИЩЕ ════════════════════════════════════════════════

@pytest.mark.parametrize("text, why", [
    ("", "пустая строка"),
    ("   ", "одни пробелы"),
    ("\n\t ", "одни переводы строки и табы"),
])
def test_pustoi_tekst_otvergnut_DO_zapisi(store, text, why):
    """Пустое задание — это молчаливая отправка пустоты лиду или вечно
    неотправимая строка в очереди, которая будет краснеть пробой до конца
    времён. Проверка обязана стоять ДО записи: строка, уже легшая в базу,
    требует человека, чтобы её оттуда убрать.

    Второе утверждение (очередь пуста) — половина, без которой сторож слеп:
    `ValueError` ПОСЛЕ `INSERT` выглядит отсюда точно так же."""
    with pytest.raises(ValueError):
        store.enqueue_outgoing(A, text, token="t-empty", now=1000.0)
    assert store.pending_outgoing() == [], (
        "задание с пустым текстом (%s) всё-таки легло в очередь: значит "
        "проверка стоит ПОСЛЕ записи, и убирать строку придётся руками" % why)


def test_pustoi_token_otvergnut_DO_zapisi(store):
    """Токен — вся идемпотентность целиком. Строка без токена не отличима от
    следующей такой же, то есть двойное нажатие кнопки перестаёт ловиться, а
    сентинел вроде `0` схлопнул бы РАЗНЫЕ задания в одно (ровно то, что уже
    случилось с панельными оплатами)."""
    with pytest.raises(ValueError):
        store.enqueue_outgoing(A, "текст есть, токена нет", token="", now=1000.0)
    assert store.pending_outgoing() == [], (
        "задание без токена легло в очередь: идемпотентности у него нет, а "
        "повтор такого задания будет неотличим от нового")


def test_povtor_po_tomu_zhe_tokenu_ne_rozhdaet_vtoruyu_stroku(store):
    """§3 п.1: одна строка — одна отправка.

    Без этого двойное нажатие кнопки в панели даёт лиду ДВА одинаковых
    сообщения от владельца. Заметит это лид, а не мы: в панели обе строки
    выглядят как одно нажатие."""
    first_id, created = _enqueue(store, A, "передумал, пишу сам", token="tok-1")
    again_id, created_again = _enqueue(store, A, "передумал, пишу сам", token="tok-1")

    assert created is True, "первое задание обязано быть НОВЫМ, а не повтором"
    assert created_again is False, (
        "повтор по тому же токену объявлен НОВЫМ заданием: панель получит "
        "«поставлено в очередь» дважды, и лид получит два сообщения")
    assert again_id == first_id, (
        "повтор по токену вернул ДРУГОЙ id (%r против %r): панель не сможет "
        "показать владельцу ту же строку, а очередь получила дубль"
        % (again_id, first_id))
    assert len(store.pending_outgoing()) == 1, (
        "в очереди %d строк вместо одной: %r"
        % (len(store.pending_outgoing()), store.pending_outgoing()))


def test_povtor_po_tokenu_ne_perepisyvaet_tekst_zadaniya(store):
    """Токен — ЛИЧНОСТЬ события, а не ключ обновления.

    Если повтор с другим текстом молча перезапишет строку, то нажатие,
    случайно повторившее чужой токен, подменит слова владельца — и он этого
    не увидит: панель ответит «уже в очереди»."""
    _enqueue(store, A, "первое, что я написал", token="tok-same")
    _enqueue(store, A, "СОВСЕМ ДРУГОЙ ТЕКСТ", token="tok-same")
    rows = store.pending_outgoing()
    assert len(rows) == 1, rows
    assert rows[0]["text"] == "первое, что я написал", (
        "повтор по токену переписал текст задания на %r — слова владельца "
        "подменились молча" % (rows[0]["text"],))


def test_dva_kontakta_zhivut_v_ocheredi_porozn(store):
    """ДВА контакта, потому что реализация может оказаться верной для одного и
    слепой для второго: очередь, адресованная «последнему контакту», отправит
    оба задания одному лиду, и второй лид не получит ничего."""
    _enqueue(store, A, "первому лиду", token="tok-a")
    _enqueue(store, B, "второму лиду", token="tok-b")
    rows = store.pending_outgoing()
    assert [(r["contact_id"], r["text"]) for r in rows] == [
        (A, "первому лиду"), (B, "второму лиду")], rows


def test_pending_otdayot_samye_starye_pervymi(store):
    """§1 контракта дословно: «САМЫЕ СТАРЫЕ ПЕРВЫМИ».

    Порядок здесь — не эстетика. Очередь разбирается порциями (`limit`), и
    при обратной сортировке самое старое задание — то самое, ради возраста
    которого заведена проба, — уезжает в хвост и не отправляется НИКОГДА,
    пока панель нажимают. Ровно эта ловушка уже стоила заниженного возраста
    в `/ops/attention`."""
    _enqueue(store, A, "третье по времени", token="t3", now=3000.0)
    _enqueue(store, B, "первое по времени", token="t1", now=1000.0)
    _enqueue(store, A, "второе по времени", token="t2", now=2000.0)
    assert [r["text"] for r in store.pending_outgoing()] == [
        "первое по времени", "второе по времени", "третье по времени"]


def test_otpravlennoe_uhodit_iz_ocheredi_i_nesyot_id_STROKOI(store):
    """`sent_msg_id` — СТРОКА (§1 контракта, §5 спеки).

    У веба и бизнес-API числовых id не будет вовсе, а у задания из панели
    своего id нет вообще. Колонка, которая сегодня «на самом деле число»,
    завтра примет `int` от Telegram и `str` от веба — и сравнение id
    перестанет работать молча, ровно в тот день, когда появится второй канал.
    """
    row_id, _ = _enqueue(store, A, "уже ушло", token="tok-sent")
    store.mark_outgoing_sent(row_id, msg_id="9001", now=1500.0)

    assert store.pending_outgoing() == [], (
        "отправленное задание осталось в очереди — следующий круг поллинга "
        "отправит его лиду второй раз")
    assert store.oldest_pending_outgoing_age(now=9999.0) is None, (
        "возраст очереди считается по отправленному заданию: проба покраснеет "
        "на пустой очереди и станет фоном")


def test_otkaz_terminalen_i_povtorov_ne_budet(store):
    """§3 п.4 и §4: отказ — СОСТОЯНИЕ строки, а не исключение.

    Две половины, и обе обязательны. Без первой («словами») владелец видит
    «не доставлено» и идёт гадать. Без второй («повторов нет») очередь будет
    вечно долбиться в закрытое окно канала — то есть тратить лимиты и держать
    пробу красной на состоянии, которое чинится не нами."""
    row_id, _ = _enqueue(store, A, "в закрытое окно", token="tok-ref")
    store.mark_outgoing_failed(
        row_id, error="окно канала закрыто: 24 часа с последнего сообщения "
                      "лида истекли, произвольный текст отправить нельзя",
        now=1500.0, terminal=True)

    assert store.pending_outgoing() == [], (
        "отказанное задание осталось в очереди: повторы пойдут по кругу")
    assert store.oldest_pending_outgoing_age(now=9999.0) is None, (
        "возраст очереди считает ОТКАЗАННОЕ задание — проба будет краснеть "
        "вечно на том, что уже решено")


def test_neterminalnyi_sboi_ostavlyaet_zadanie_v_ocheredi_i_schitaet_popytki(store):
    """`terminal=False` — это «канал моргнул», и задание обязано пережить
    моргание: владелец нажал, и тишина в ответ на временный сбой сети — это
    ровно та «побочная выгода, которая дороже самой отправки» (§2 спеки),
    потерянная зря.

    Счётчик попыток здесь не украшение: без него «моргает» и «не работает
    никогда» неотличимы, и вторая беда будет вечно выглядеть первой."""
    row_id, _ = _enqueue(store, A, "переживёт моргание", token="tok-retry")
    store.mark_outgoing_failed(row_id, error="сеть недоступна", now=1500.0,
                              terminal=False)
    rows = store.pending_outgoing()
    assert len(rows) == 1, (
        "временный сбой убрал задание из очереди — владелец нажал, а оно не "
        "уйдёт никогда; %r" % (rows,))
    assert rows[0]["attempts"] == 1, (
        "попытки не считаются (attempts=%r): «моргает» и «не работает "
        "никогда» станут неотличимы" % (rows[0]["attempts"],))
    assert rows[0]["last_error"], "причина временного сбоя потеряна"

    store.mark_outgoing_failed(row_id, error="сеть недоступна", now=1600.0,
                              terminal=False)
    assert store.pending_outgoing()[0]["attempts"] == 2, (
        "второй сбой не увеличил счётчик: %r" % (store.pending_outgoing(),))


def test_vozrast_samogo_starogo_zadaniya_rastyot(store):
    """§3 п.3: «задание не протухает молча».

    Возраст — единственная величина, по которой видно, что владелец нажал, а
    оно не ушло. `None` на пустой очереди — это НЕ ноль: ноль секунд читается
    как «только что положили», то есть как самая свежая беда вместо её
    отсутствия."""
    assert store.oldest_pending_outgoing_age(now=1000.0) is None, (
        "пустая очередь отдала число вместо None: «заданий нет» и «задание "
        "только что положили» станут неотличимы")

    _enqueue(store, A, "лежит и ждёт", token="tok-age", now=1000.0)
    assert store.oldest_pending_outgoing_age(now=1000.0) == pytest.approx(0.0)
    assert store.oldest_pending_outgoing_age(now=1000.0 + DAY) == pytest.approx(DAY)

    _enqueue(store, B, "положено позже", token="tok-age2", now=1000.0 + DAY)
    assert store.oldest_pending_outgoing_age(now=1000.0 + 2 * DAY) == pytest.approx(2 * DAY), (
        "возраст взят у МОЛОДОГО задания: величина, которая тем меньше, чем "
        "хуже дела, — это сторож, врущий ровно в аварии")


# ═══ §2 контракта: ЕДИНАЯ ТОЧКА ПЕРЕХВАТА ═══════════════════════════════════

def test_open_human_takeover_glushit_kontakt_i_nazyvaet_prichinu(store):
    """Эпизод перехвата — это МУТ ПЛЮС АТРИБУЦИЯ, и без второй половины пауза
    становится безымянной: на вопрос «почему бот молчит» ответа нет, а
    самозаглушка тиха и вечна."""
    ok = _need("open_human_takeover")(store, A, msg_id=42,
                                      detail="я отвечу сам", now=1000.0)
    row = store.get_or_create_contact(A)
    assert ok is True, "первый эпизод обязан быть НАШИМ"
    assert row["paused"] == 1, (
        "контакт не заглушён после открытия перехвата: бот продолжит "
        "сочинять поверх человека; %r" % (dict(row),))
    assert row["pause_source"] == "human_takeover", (
        "источник паузы %r вместо 'human_takeover': /status и панель покажут "
        "чужую причину, а авто-возврат отсчитает не от того момента"
        % (row["pause_source"],))
    assert row["pause_detail"] == "я отвечу сам", dict(row)


def test_vtoroe_otkrytie_toho_zhe_epizoda_ne_nashe(store):
    """Один эпизод — один победитель. Владелец, отправивший из панели три
    сообщения подряд, не должен получить три карточки и три события: это тот
    же залп, который уже разбирает `begin_takeover`."""
    open_takeover = _need("open_human_takeover")
    assert open_takeover(store, A, msg_id=1, detail="раз", now=1000.0) is True
    assert open_takeover(store, A, msg_id=2, detail="два", now=1001.0) is False, (
        "второе открытие объявило эпизод своим: владелец получит вторую "
        "карточку и второе событие об ОДНОМ перехвате")


def test_chuzhaya_pauza_ne_perepisyvaetsya_perehvatom(store):
    """Диалог, заглушённый ДРУГОЙ причиной, перехват не присваивает.

    Иначе `/pause` владельца превратится в `human_takeover`, и авто-возврат
    (который бессрочную команду отменять не вправе) разморозит диалог у него
    под руками."""
    store.mute(A, source="command", now=900.0)
    ok = _need("open_human_takeover")(store, A, msg_id=7, detail="поверх",
                                      now=1000.0)
    row = store.get_or_create_contact(A)
    assert ok is False, "эпизод объявлен нашим поверх чужой паузы"
    assert row["pause_source"] == "command", (
        "чужая атрибуция переписана на %r: команда владельца превратилась в "
        "перехват, и таймер авто-возврата теперь вправе её снять"
        % (row["pause_source"],))


def test_perehvat_kazhdogo_kontakta_svoi(store):
    """Перехват ОДНОГО диалога не смеет глушить второй: иначе один ответ
    владельца выключает бота на всей воронке, и это выглядит как
    `kill_switch`, которого никто не нажимал."""
    _need("open_human_takeover")(store, A, msg_id=1, detail="только сюда",
                                 now=1000.0)
    assert store.get_or_create_contact(B)["paused"] == 0, (
        "перехват первого контакта заглушил второй — бот замолчал в диалоге, "
        "где человека нет")


def test_begin_takeover_zovyotsya_ROVNO_IZ_ODNOGO_MESTA():
    """§2 контракта: «Второго пути открытия эпизода в дереве быть не должно».

    Два пути — это два набора побочных действий (событие, атрибуция,
    карточка), и разъедутся они не в день правки, а в день, когда правку
    внесут только в один из них. Разбор по AST, а не поиском подстроки:
    подстрока совпала бы и в комментарии, объясняющем, почему второго пути
    быть не должно (буква «И» в комментарии уже делала пойманную мутацию
    слепой, [[jarvis-mutation-gate-lies-third-way-cp1251]]).
    """
    tree = ast.parse(RUNNER_SRC.read_text(encoding="utf-8"))
    callers: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(node):
            if (isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == "begin_takeover"):
                callers.add(node.name)
    assert callers == {"open_human_takeover"}, (
        "`begin_takeover` зовут из %s, а контракт §2 требует РОВНО одну точку "
        "`open_human_takeover`: второй путь получит свой набор побочных "
        "действий и разъедется с первым молча" % (sorted(callers) or "ниоткуда",))


def test_staryi_put_chuzhogo_ishodyashchego_hodit_cherez_tu_zhe_tochku(store):
    """`on_human_takeover` обязан ходить через `open_human_takeover` (§2).

    Пока старый путь открывает эпизод сам, «единая точка» — это ещё один путь
    рядом, а не единая точка. Проверяется ПОДМЕНОЙ функции: сторож на текст
    сказал бы лишь, что имя где-то упомянуто."""
    runner, _sends, _notifier = _runner(store)
    seen: list[tuple] = []
    real = _need("open_human_takeover")

    def spy(st, contact_id, *, msg_id, detail, now):
        seen.append((contact_id, msg_id))
        return real(st, contact_id, msg_id=msg_id, detail=detail, now=now)

    event = _EventDouble(raw_text="я вмешался руками", msg_id=555, chat_id=111)

    original = tr.open_human_takeover
    tr.open_human_takeover = spy
    try:
        _run(runner.on_human_takeover(event, A))
    finally:
        tr.open_human_takeover = original

    assert seen == [(A, 555)], (
        "`on_human_takeover` открыл эпизод МИМО единой точки: побочные "
        "действия двух путей разъедутся, и заметит это только клиент; %r"
        % (seen,))


# ═══ §3 контракта: ДОСТАВКА ═════════════════════════════════════════════════

def test_zadanie_iz_paneli_porozhdaet_ROVNO_ODNU_otpravku(store):
    """§10 п.1. Один прогон доставки — одно сообщение лиду, не два и не ноль.

    Ноль здесь тише двойки: панель уже сказала владельцу «поставлено», и
    несостоявшаяся отправка выглядит как отправленная."""
    async def scenario():
        runner, sends, _n = _runner(store)
        _enqueue(store, A, "здравствуйте, это владелец", token="tok-one")
        row = store.pending_outgoing()[0]
        verdict = await _need("deliver_outgoing")(runner, row, now=1000.0)
        return verdict, sends

    verdict, sends = _run(scenario())
    assert verdict == "sent", (
        "вердикт доставки %r вместо 'sent' — контракт §3 разрешает РОВНО три "
        "слова: 'sent' | 'refused' | 'retry'" % (verdict,))
    assert len(sends.to_lead("здравствуйте, это владелец")) == 1, (
        "лид увидел %d сообщений вместо одного: %r"
        % (len(sends.to_lead("здравствуйте, это владелец")), sends.calls))
    assert store.pending_outgoing() == [], (
        "отправленное задание осталось pending — следующий круг поллинга "
        "отправит его снова, и лид получит дубль")


def test_povtornyi_krug_pollinga_ne_daet_vtorogo_soobsheniya(store):
    """§10 п.1, вторая половина: поллинг крутится КАЖДЫЕ 5 СЕКУНД.

    Идемпотентность по токену защищает от двойного нажатия, а этот сторож —
    от собственного цикла: строка, не ушедшая из `pending` после успеха,
    порождает по сообщению каждые пять секунд, и остановит это только человек.
    """
    async def scenario():
        runner, sends, _n = _runner(store)
        _enqueue(store, A, "одно и то же", token="tok-poll")
        _enqueue(store, A, "одно и то же", token="tok-poll")   # двойное нажатие
        deliver = _need("deliver_outgoing")
        for _ in range(3):                                     # три круга поллинга
            for row in store.pending_outgoing():
                await deliver(runner, row, now=1000.0)
        return sends

    sends = _run(scenario())
    assert len(sends.to_lead("одно и то же")) == 1, (
        "за три круга поллинга и два нажатия лид получил %d сообщений: %r"
        % (len(sends.to_lead("одно и то же")), sends.calls))


def test_dva_zadaniya_dvum_lidam_ne_shlyutsya_odnomu(store):
    """Реализация, адресующая отправку «последним контактом», верна для одного
    лида и слепа для второго: первый получит чужие слова, второй — ничего."""
    async def scenario():
        runner, sends, _n = _runner(store)
        _enqueue(store, A, "первому: ваш заказ готов", token="tok-a")
        _enqueue(store, B, "второму: перезвоню завтра", token="tok-b")
        deliver = _need("deliver_outgoing")
        for row in store.pending_outgoing():
            await deliver(runner, row, now=1000.0)
        return sends

    sends = _run(scenario())
    assert len(sends.to_lead("первому: ваш заказ готов")) == 1, sends.calls
    assert len(sends.to_lead("второму: перезвоню завтра")) == 1, sends.calls
    chats = {c[0] for c in sends.to_lead()}
    assert len(chats) == 2, (
        "оба задания уехали в ОДИН чат %r: один лид получил чужие слова "
        "владельца, второй не получил ничего" % (chats,))


def test_otpravka_iz_paneli_OTKRYVAET_epizod_perehvata(store):
    """§10 п.2. Без этого бот продолжит отвечать поверх человека — тише и
    опаснее лишней карточки, потому что заметит это только клиент, получивший
    два разных ответа на один вопрос."""
    async def scenario():
        runner, _s, _n = _runner(store)
        _enqueue(store, A, "дальше я сам", token="tok-take")
        await _need("deliver_outgoing")(runner, store.pending_outgoing()[0], now=1000.0)

    _run(scenario())
    row = store.get_or_create_contact(A)
    assert row["paused"] == 1, (
        "после отправки из панели контакт НЕ заглушён: бот ответит поверх "
        "владельца, и оба ответа увидит клиент; %r" % (dict(row),))
    assert row["pause_source"] == "human_takeover", (
        "источник паузы %r: панель поставила паузу мимо перехвата, и "
        "авто-возврат посчитает её командой" % (row["pause_source"],))
    assert row["pause_detail"] and "дальше я сам" in str(row["pause_detail"]), (
        "атрибуция не указывает на ЭТО задание (%r): в /status и панели "
        "владелец увидит паузу без своих слов" % (row["pause_detail"],))
    assert store.get_or_create_contact(B)["paused"] == 0, (
        "отправка одному лиду заглушила второй диалог")


def test_PEREHVAT_OTKRYVAETSYA_DO_OTPRAVKI(store):
    """🔴 ПОРЯДОК (§10 п.9, §3 контракта дословно).

    Обратный порядок оставляет ОКНО: сообщение владельца уже у лида, а бот ещё
    не знает о человеке. В это окно попадает ровно то, ради чего вся арка, —
    входящее лида, на которое бот бросится отвечать сам. Окно узкое, поэтому
    живьём оно не воспроизводится и в приёмке невидимо; поймать его можно
    только замером В МОМЕНТ отправки.

    Проверка по состоянию ПОСЛЕ доставки не годится: после верны ОБА порядка.
    """
    async def scenario():
        runner, sends, _n = _runner(store)
        _enqueue(store, A, "порядок важен", token="tok-order")
        await _need("deliver_outgoing")(runner, store.pending_outgoing()[0], now=1000.0)
        return sends

    sends = _run(scenario())
    assert sends.state_at_send, (
        "отправки не было вовсе — порядок мерить не на чем")
    at_send = sends.state_at_send[0]
    assert at_send["paused"] == 1 and at_send["pause_source"] == "human_takeover", (
        "В МОМЕНТ отправки контакт ещё не был заглушён (paused=%r, "
        "source=%r): между отправкой и перехватом осталось окно, в котором "
        "бот не знает о человеке и ответит поверх него"
        % (at_send["paused"], at_send["pause_source"]))


def test_sobstvennaya_otpravka_ne_chitaetsya_kak_chuzhoe_ishodyashchee(store):
    """§7 спеки, первая ветка развилки.

    Не зарегистрировали своё исходящее — раннер увидит его как чужое, поднимет
    ВТОРУЮ атрибуцию и карточку на сообщение, которое сам же отправил по
    кнопке владельца. Механика различения одна на всё дерево — `SentRegistry`,
    и спрашивается она той же `decide_outgoing`, которой живёт раннер."""
    async def scenario():
        runner, sends, _n = _runner(store)
        _enqueue(store, A, "это отправил я через панель", token="tok-ours")
        await _need("deliver_outgoing")(runner, store.pending_outgoing()[0], now=1000.0)
        sent_id = sends.next_id
        verdict = await decide_outgoing(sent_id, registry=runner.sent_registry,
                                        grace_seconds=0.01)
        return verdict, sent_id, runner

    verdict, sent_id, runner = _run(scenario())
    assert isinstance(runner.sent_registry, SentRegistry)
    assert verdict == "ours", (
        "id %r собственной отправки не опознан как свой (вердикт %r): раннер "
        "поднимет перехват «чужого исходящего» на нажатие владельца"
        % (sent_id, verdict))


def test_KARTOCHKI_NET_a_SOBYTIE_takeover_EST(store):
    """🔴 §9.2, решение владельца: обе половины в ОДНОМ стороже.

    Половина «карточки нет»: лишний вопрос на собственное нажатие учит жать
    «да» не глядя, и следующий вопрос, который правда требовал внимания, будет
    прожат так же ([[jarvis-ask-bridge-auto-allow-suspect]]).

    Половина «событие есть»: без записи в журнале перехват из панели
    невидим — разбор «кто это написал» упирается в догадку, а панель не может
    показать эпизод вовсе.

    Порознь эти половины были бы двумя сторожами, из которых удобно удалить
    неудобный: «карточки нет» проходит и на реализации, которая не сделала
    НИЧЕГО.
    """
    async def scenario():
        runner, sends, notifier = _runner(store)
        _enqueue(store, A, "тихий перехват", token="tok-card")
        await _need("deliver_outgoing")(runner, store.pending_outgoing()[0], now=1000.0)
        return sends, notifier

    sends, notifier = _run(scenario())

    assert sends.cards() == [], (
        "владельцу ушла карточка на его же нажатие: %r" % (sends.cards(),))
    assert notifier.cards == [], (
        "карточка ушла через пульт (%d штук): владелец получает вопрос о "
        "действии, которое сам только что совершил" % (len(notifier.cards),))
    cards = store._conn.execute("SELECT * FROM console_cards").fetchall()
    assert list(cards) == [], (
        "карточка записана в `console_cards` — значит она была показана; %r"
        % ([dict(c) for c in cards],))

    events = store._conn.execute(
        "SELECT kind, contact_id FROM control_events WHERE kind='takeover'"
    ).fetchall()
    assert [tuple(e) for e in events] == [("takeover", A)], (
        "события `takeover` в журнале нет (%r): перехват из панели невидим, "
        "и разбор «кто это написал» упирается в догадку"
        % ([tuple(e) for e in events],))


def test_bot_s_otkrytym_epizodom_svoi_otvet_NE_SHLYOT(store):
    """§10 п.4 / §6 контракта: проверка стоит в РАННЕРЕ, перед самой отправкой.

    Между решением панели и отправкой проходит цикл поллинга, и состояние
    успевает измениться. Проверка, стоящая в панели, права в момент нажатия и
    слепа через пять секунд — а именно эти пять секунд и есть весь риск.

    Второй контакт в стенде обязателен: реализация, заглушившая бота ВЕЗДЕ,
    прошла бы половину сторожа и выглядела бы как `kill_switch`, которого
    никто не нажимал.
    """
    _need("open_human_takeover")(store, A, msg_id=1, detail="я сам", now=1000.0)

    deps_a = _persona("demo", store, scripted=["НЕ ДОЛЖНО УЙТИ ЛИДУ"]).deps
    transport_a = FakeConsoleTransport(preload=[], echo=False)
    process_batch(A, ["а сколько стоит?"], transport_a, deps_a)
    assert transport_a.sent == [], (
        "бот заговорил поверх человека, у которого открыт эпизод перехвата: "
        "клиент получит два разных ответа на один вопрос; %r" % (transport_a.sent,))

    deps_b = _persona("demo", store, scripted=["Добрый день! Чем помочь?"]).deps
    transport_b = FakeConsoleTransport(preload=[], echo=False)
    process_batch(B, ["а сколько стоит?"], transport_b, deps_b)
    assert transport_b.sent, (
        "бот замолчал и во ВТОРОМ диалоге, где человека нет: перехват одного "
        "контакта выключил всю воронку")


def test_KILL_SWITCH_i_FUNNEL_GATE_ne_meshayut_cheloveku_i_PRODOLZHAYUT_glushit_bota(store):
    """🔴 §8 спеки: обе половины в ОДНОМ стороже, и это не стиль.

    Половина первая: владелец, ВЫКЛЮЧИВШИЙ бота, обязан иметь возможность
    ответить клиенту сам — ровно тогда, когда это нужнее всего. Тумблеры
    выключают БОТА, а не человека.

    Половина вторая: и при этом они продолжают глушить бота. В спеке веба
    §12.2 записано «`funnel_gate` не обходится», и сторож, написанный по той
    строке буквально, запретил бы отправку человеку и был бы по-своему прав.
    Обратная ошибка тише: «человек проходит» легко превратить в «проходит
    кто угодно», и бот заговорит при взведённом рубильнике.

    Порознь эти половины разъедутся: удалить неудобную из двух — правка на
    одну строку, и вторая останется зелёной.
    """
    store.set_runtime_flag("kill_switch", "1", ts=1000.0)

    async def scenario():
        runner, sends, _n = _runner(store, funnel_gate=True)
        _enqueue(store, A, "бот выключен, отвечаю я", token="tok-kill")
        verdict = await _need("deliver_outgoing")(
            runner, store.pending_outgoing()[0], now=1000.0)
        return verdict, sends

    verdict, sends = _run(scenario())
    assert verdict == "sent", (
        "человек не прошёл поверх тумблеров (вердикт %r): владелец, "
        "выключивший бота, не может ответить клиенту сам" % (verdict,))
    assert len(sends.to_lead("бот выключен, отвечаю я")) == 1, sends.calls

    # ── вторая половина: тумблеры ПРОДОЛЖАЮТ глушить бота ──────────────────
    deps = _persona("demo", store, scripted=["НЕ ДОЛЖНО УЙТИ ЛИДУ"]).deps
    transport = FakeConsoleTransport(preload=[], echo=False)
    process_batch(B, ["привет"], transport, deps)
    assert transport.sent == [], (
        "при взведённом kill_switch бот всё-таки заговорил: рубильник, "
        "который глушит не всех, — это рубильник, которому нельзя верить; %r"
        % (transport.sent,))

    assert admission_decision(sender_id=333, is_contact=True,
                              allowlist=frozenset(), denylist=frozenset(),
                              funnel_gate=True) != "answer", (
        "`funnel_gate` перестал глушить бота на знакомом отправителе: гейт "
        "воронки открылся заодно с отправкой человека")


# ── §4 контракта: ОТКАЗЫ СЛОВАМИ ────────────────────────────────────────────

def _refusal(reason: str, human: str):
    """Собрать `Refusal` контракта §4.

    Форма конструктора контрактом не названа (это законный выбор автора кода),
    названы АТРИБУТЫ — `reason` и `human`. Пробуем обе очевидные формы и
    требуем именно атрибутов: без них отказ снова становится безымянным."""
    Refusal = _need("Refusal")
    for build in (lambda: Refusal(reason=reason, human=human),
                  lambda: Refusal(reason, human)):
        try:
            exc = build()
        except TypeError:
            continue
        if getattr(exc, "reason", None) == reason and getattr(exc, "human", None) == human:
            return exc
    raise AssertionError(
        "`Refusal` не собирается ни как Refusal(reason=..., human=...), ни как "
        "Refusal(%r, %r) с этими же атрибутами: контракт §4 требует у отказа "
        "именно `reason` (ключ) и `human` (текст владельцу)" % (reason, human))


def _refuse(store, contact_id: str, text: str, *, token: str,
            create_contact: bool = True):
    """Прогнать ОДНО задание до отказа и вернуть (вердикт, строка, отправки)."""
    if create_contact:
        store.get_or_create_contact(contact_id)
    _enqueue(store, contact_id, text, token=token)

    async def scenario():
        runner, sends, _n = _runner(store)
        verdict = await _need("deliver_outgoing")(
            runner, store.pending_outgoing()[0], now=1000.0)
        return verdict, sends

    verdict, sends = _run(scenario())
    row = store._conn.execute("SELECT * FROM outgoing_queue").fetchone()
    return verdict, dict(row), sends


def _assert_named_refusal(verdict, row, sends, *, what: str):
    """Общая половина всех четырёх границ §8: отказ НАЗВАН и ТЕРМИНАЛЕН.

    Собрано в одну функцию не ради краткости, а чтобы четыре границы
    проверялись ОДНОЙ меркой: разъехавшись, они дали бы владельцу четыре
    разных представления о том, что значит «не доставлено»."""
    assert verdict == "refused", (
        "%s дало вердикт %r вместо 'refused'" % (what, verdict))
    assert row["status"] == "refused", (
        "%s оставило строку в статусе %r — повторы пойдут по кругу"
        % (what, row["status"]))
    assert row["last_error"] and any(c.isalpha() for c in str(row["last_error"])), (
        "%s не объяснено словами (last_error=%r): владелец увидит «не "
        "доставлено» и пойдёт гадать" % (what, row["last_error"]))
    assert sends.to_lead() == [], (
        "%s, а сообщение всё-таки ушло лиду: %r" % (what, sends.calls))


def test_vyklyuchennyi_klient_otkazyvaet_SLOVAMI(store):
    """`client_disabled`: клиента нет среди персон процесса.

    Так выглядит выключенный в ростере клиент и клиент, живущий в другом
    процессе. Граница настоящая: отправлять физически некуда, потому что
    некому. Молча оставить задание `pending` значило бы копить очередь,
    которая не уедет никогда, и держать пробу красной без причины, которую
    можно назвать.
    """
    verdict, row, sends = _refuse(store, "333:ghost", "клиенту, которого нет",
                                  token="tok-disabled")
    _assert_named_refusal(verdict, row, sends, what="задание выключённому клиенту")


def test_nepodklyuchennyi_kanal_otkazyvaet_SLOVAMI(store):
    """`no_transport`: у клиента нет блока `telegram` — канал не подключён.

    Вторая граница §8 спеки, поверх которой человек НЕ проходит. Её легко
    спутать с тумблером: и там и там «бот молчит». Разница в том, что тумблер
    выключил ЧЕЛОВЕК и человек же проходит поверх него, а здесь отправлять
    физически нечем.
    """
    verdict, row, sends = _refuse(store, "444:mute", "в неподключённый канал",
                                  token="tok-notransport")
    _assert_named_refusal(verdict, row, sends, what="задание в неподключённый канал")


def test_neznakomyi_dialog_otkazyvaet_SLOVAMI(store):
    """`unknown_contact`: диалога, которому адресовано задание, раннер не знает.

    Отправить «куда-нибудь» здесь опаснее, чем не отправить: это чужой диалог
    с аккаунта клиента, и вернуть такое сообщение нельзя. Отказ обязан быть
    громким и терминальным.
    """
    verdict, row, sends = _refuse(store, "555:demo", "в незнакомый диалог",
                                  token="tok-unknown", create_contact=False)
    _assert_named_refusal(verdict, row, sends, what="задание в незнакомый диалог")


def test_nerazbiraemyi_contact_id_eto_tozhe_otkaz_SLOVAMI(store):
    """`unknown_contact` и для мусора вместо `"<peer_id>:<slug>"`.

    Дополнение к контракту §3 называет формат явно. Мусор в адресате обязан
    стать НАЗВАННЫМ отказом, а не исключением из цикла: исключение остановило
    бы доставку ВСЕМ остальным лидам, и остановка была бы молчаливой.
    """
    verdict, row, sends = _refuse(store, "bez-dvoetochiya", "адресат — мусор",
                                  token="tok-garbage", create_contact=False)
    _assert_named_refusal(verdict, row, sends, what="неразбираемый адресат")


@pytest.mark.parametrize("reason", REFUSAL_REASONS)
def test_otkaz_lozhitsya_v_stroku_SLOVAMI_i_povtorov_net(store, reason):
    """§10 п.8 и п.10: «не доставлено» без причины отправляет владельца гадать.

    Разбито параметрами намеренно: одна отвалившаяся причина обязана называть
    СЕБЯ, а не тонуть в общем «отказ как-то не сработал». Набор причин —
    ЛИТЕРАЛЬНЫЙ (§4 контракта, «шире не выдумывать»): выведенный из кода
    список согласен с кодом по определению и промолчал бы ровно там, где код
    забыл причину.

    Три утверждения в одном: вердикт назван словом контракта, строка стала
    `refused`, а человеческий текст доехал до `last_error`. Без третьего
    владелец увидит «не доставлено» и пойдёт гадать; без второго очередь будет
    вечно долбиться в состояние, которое чинится не нами.
    """
    human = "отказ по причине %s: объяснение для владельца словами" % reason

    async def scenario():
        runner, sends, _n = _runner(store)
        sends.raises = _refusal(reason, human)
        _enqueue(store, A, "текст, который не уедет", token="tok-" + reason)
        verdict = await _need("deliver_outgoing")(
            runner, store.pending_outgoing()[0], now=1000.0)
        return verdict

    verdict = _run(scenario())
    assert verdict == "refused", (
        "отказ по причине %r дал вердикт %r: контракт §3 требует РОВНО "
        "'sent' | 'refused' | 'retry'" % (reason, verdict))

    rows = store._conn.execute(
        "SELECT status, last_error FROM outgoing_queue").fetchall()
    assert len(rows) == 1, rows
    status, last_error = rows[0]["status"], rows[0]["last_error"]
    assert status == "refused", (
        "строка после отказа %r осталась в статусе %r: повторы пойдут по "
        "кругу, а проба будет краснеть на уже решённом" % (reason, status))
    assert last_error and human in str(last_error), (
        "человеческое объяснение отказа не доехало до строки (last_error=%r): "
        "владелец увидит «не доставлено» и пойдёт гадать" % (last_error,))
    assert store.pending_outgoing() == [], (
        "отказанное задание осталось в очереди — повторы по кругу")


def test_otkaz_ne_uletaet_isklyucheniem_naruzhu(store):
    """§4 контракта: «Отказ = состояние строки, а НЕ исключение наружу».

    Исключение, вылетевшее из доставки, убивает круг поллинга: одно закрытое
    окно канала останавливает отправку ВСЕМ остальным лидам, и остановка эта
    молчалива."""
    async def scenario():
        runner, sends, _n = _runner(store)
        sends.raises = _refusal("no_transport", "канал не подключён")
        _enqueue(store, A, "некуда слать", token="tok-raise")
        return await _need("deliver_outgoing")(
            runner, store.pending_outgoing()[0], now=1000.0)

    verdict = _run(scenario())
    assert verdict == "refused", verdict


def test_MOLCHALIVYI_otkaz_zapreshchen_dazhe_na_neozhidannoi_oshibke(store):
    """🔴 Неопознанный сбой не имеет права выглядеть успехом (DEV-18).

    Названные причины — это то, что мы предусмотрели. Настоящая авария придёт
    неназванной, и именно на ней «проглотили исключение и пошли дальше»
    превращается в «панель сказала отправлено, лид не получил ничего». Это
    худшая из трёх бед файла: она врёт владельцу его же руками.
    """
    async def scenario():
        runner, sends, _n = _runner(store)
        sends.raises = RuntimeError("телефонная будка провалилась в тартарары")
        _enqueue(store, A, "исчезнет молча?", token="tok-boom")
        return await _need("deliver_outgoing")(
            runner, store.pending_outgoing()[0], now=1000.0)

    verdict = _run(scenario())
    assert verdict in ("refused", "retry"), (
        "неопознанный сбой доставки дал вердикт %r: панель показала владельцу "
        "успех там, где лид не получил ничего" % (verdict,))

    row = store._conn.execute("SELECT * FROM outgoing_queue").fetchone()
    assert row["status"] != "sent", (
        "строка помечена отправленной при упавшей отправке: %r" % (dict(row),))
    assert row["last_error"], (
        "причина неопознанного сбоя не записана: разбор начнётся с догадки, "
        "а панель покажет пустоту вместо объяснения; %r" % (dict(row),))


def test_prichiny_otkaza_ne_pridumyvayutsya_shire_kontrakta():
    """§4 контракта: набор `reason` литеральный, «шире не выдумывать».

    `reason` — ключ дедупа алертов и ключ, по которому чинят. Причина, которую
    завели «на всякий случай», проедет мимо всех правил дедупа и станет шумом,
    а шум однажды спрячет настоящее.

    Пин ОДНОСТОРОННИЙ и это названо вслух: «нет лишних» проверяется здесь,
    «есть все четыре» проверить нечем, пока не все четыре достижимы в фазе 0
    (§«Границы фазы 0» контракта: канал один).
    """
    tree = ast.parse(RUNNER_SRC.read_text(encoding="utf-8"))
    used: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "Refusal"):
            continue
        literals = [a.value for a in node.args
                    if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        literals += [kw.value.value for kw in node.keywords
                     if kw.arg == "reason" and isinstance(kw.value, ast.Constant)]
        if literals:
            used.add(literals[0])
    assert used, (
        "в `telethon_run.py` не поднимается ни одного `Refusal` с литеральной "
        "причиной — отказы либо безымянны, либо их нет вовсе")
    assert used <= set(REFUSAL_REASONS), (
        "заведены причины вне контракта §4: %s. `reason` — ключ дедупа, и "
        "непредусмотренный ключ проедет мимо всех правил склейки алертов"
        % sorted(used - set(REFUSAL_REASONS)))


# ── §10 п.7: ЛЕЖАЧИЙ РАННЕР ─────────────────────────────────────────────────

def test_lezhachii_runner_zadanie_zhivyot_vozrast_rastyot_i_uhodit_ODIN_raz(store):
    """🔴 «Владелец нажал, а оно не ушло» перестаёт быть тишиной (§2 спеки).

    Это та самая побочная выгода, которая дороже самой отправки. Три
    утверждения подряд, и каждое ломается отдельно:

    * задание ПЕРЕЖИЛО простой — иначе панель солгала «поставлено»;
    * возраст РОС — иначе проба не покраснеет и простой останется невидим;
    * после подъёма ушло РОВНО ОДИН раз — иначе накопленная за простой
      очередь выстрелит лиду залпом, и чем дольше лежали, тем хуже залп.
    """
    _enqueue(store, A, "лежало, пока раннер был мёртв", token="tok-down",
             now=1000.0)

    assert len(store.pending_outgoing()) == 1, (
        "задание не пережило простой раннера: панель сказала владельцу "
        "«поставлено», и это была ложь")
    assert store.oldest_pending_outgoing_age(now=1000.0 + 2 * DAY) == pytest.approx(2 * DAY), (
        "возраст задания за двое суток простоя не вырос: проба не покраснеет, "
        "и простой останется невидимым")

    async def scenario():
        runner, sends, _n = _runner(store)
        deliver = _need("deliver_outgoing")
        for _ in range(2):                       # раннер поднялся, круги пошли
            for row in store.pending_outgoing():
                await deliver(runner, row, now=1000.0 + 2 * DAY)
        return sends

    sends = _run(scenario())
    assert len(sends.to_lead("лежало, пока раннер был мёртв")) == 1, (
        "после подъёма задание ушло %d раз: накопленное за простой выстрелит "
        "лиду залпом, и чем дольше лежали, тем хуже залп"
        % len(sends.to_lead("лежало, пока раннер был мёртв")))


def test_sboi_odnoi_stroki_ne_ubivaet_cikl_dostavki(store):
    """DEV-18 и §3 контракта: «сбой одной строки не убивает цикл».

    Цикл — вечный фон рядом с `heartbeat_loop`. Умерший цикл не роняет
    процесс: гардиан видит живой раннер и живой heartbeat, а отправка из
    панели просто перестаёт происходить — молча и до тех пор, пока кто-нибудь
    не откроет панель. Ровно тот класс, что
    [[jarvis-loud-failure-next-to-a-soothing-lamp]].

    Цикл заводится с инъектируемым сном (контракт §3: `async_sleep`), иначе
    сторож ждал бы настоящие пять секунд и остановить его было бы нечем.
    """
    _enqueue(store, A, "эта строка взорвётся", token="tok-bad", now=1000.0)
    _enqueue(store, B, "эта строка обязана уехать", token="tok-good", now=1001.0)

    async def scenario():
        runner, sends, _n = _runner(store)
        deliver = _need("deliver_outgoing")

        async def exploding(rnr, row, *, now):
            if row["contact_id"] == A:
                raise RuntimeError("взрыв на первой строке")
            return await deliver(rnr, row, now=now)

        ticks = {"n": 0}

        async def fake_sleep(_seconds):
            ticks["n"] += 1
            if ticks["n"] >= 3:
                raise asyncio.CancelledError()

        original = tr.deliver_outgoing
        tr.deliver_outgoing = exploding
        try:
            with pytest.raises(asyncio.CancelledError):
                await _need("outgoing_loop")(runner, interval=0.0,
                                             async_sleep=fake_sleep)
        finally:
            tr.deliver_outgoing = original
        return sends, ticks

    sends, ticks = _run(scenario())
    assert ticks["n"] >= 2, (
        "цикл не пережил взрыв первой строки (кругов: %d): отправка из панели "
        "прекратилась молча, а heartbeat и гардиан по-прежнему зелены"
        % ticks["n"])
    assert len(sends.to_lead("эта строка обязана уехать")) >= 1, (
        "вторая строка так и не уехала: одна взорвавшаяся строка забрала с "
        "собой всю очередь; %r" % (sends.calls,))


def test_interval_pollinga_nazvan_odnim_chislom():
    """Контракт §3: `OUTGOING_POLL_INTERVAL_SECONDS = 5.0`.

    Число названо в контракте, потому что от него зависит ОКНО между
    нажатием и отправкой — то самое, внутри которого состояние диалога
    успевает измениться (§7 спеки). Второе такое число рядом погасило бы
    первое молча ([[jarvis-two-numbers-for-one-thing]])."""
    assert getattr(tr, "OUTGOING_POLL_INTERVAL_SECONDS", None) == 5.0, (
        "интервал поллинга исходящих — %r вместо 5.0 из контракта §3"
        % (getattr(tr, "OUTGOING_POLL_INTERVAL_SECONDS", None),))


# ═══ §7 спеки: СНЯТИЕ ПАУЗЫ ═════════════════════════════════════════════════

def test_snyatie_pauzy_iz_paneli_i_resume_iz_telegram_dayut_ODINAKOVOE_sostoyanie(store):
    """§10 п.8: «Оба пути ведут в один `Store` — это пин, а не пожелание».

    Разъехавшись, они дадут владельцу ДВА разных диалога, выглядящих одинаково
    снятыми с паузы: один бот продолжит, во втором останется хвост атрибуции,
    от которого авто-возврат отсчитает не тот момент, а `/status` покажет
    паузу, которой нет.

    Сравниваются ПОЛНЫЕ строки контакта, а не отдельные поля: сторож на
    `paused == 0` промолчал бы ровно про хвост, который и есть беда.
    """
    open_takeover = _need("open_human_takeover")
    open_takeover(store, A, msg_id=11, detail="перехват один", now=1000.0)
    open_takeover(store, B, msg_id=22, detail="перехват два", now=1000.0)
    assert store.get_or_create_contact(A)["paused"] == 1
    assert store.get_or_create_contact(B)["paused"] == 1

    # Путь панели: та же кнопка, которой владелец жмёт «Вернуть бота».
    route_callback("%s:%s" % (Action.RESUME.value, A), store=store, now=2000.0,
                   language="ru", snooze_seconds=3600.0)
    # Путь Telegram: /resume реплаем на карточку.
    tr.execute_command(Command(name="resume"), store=store, contact_id=B,
                       now=2000.0)

    from_panel = dict(store.get_or_create_contact(A))
    from_telegram = dict(store.get_or_create_contact(B))
    from_panel.pop("contact_id")
    from_telegram.pop("contact_id")

    assert from_panel == from_telegram, (
        "панель и Telegram оставили РАЗНОЕ состояние контакта.\n"
        "  из панели:   %r\n  из Telegram: %r\n"
        "Один из двух диалогов несёт хвост, от которого авто-возврат "
        "отсчитает не тот момент, а /status покажет паузу, которой нет"
        % (from_panel, from_telegram))
    assert from_panel["paused"] == 0, (
        "оба пути оставили контакт заглушённым — сторож на равенство сошёлся "
        "бы и на двух одинаково сломанных путях; %r" % (from_panel,))
    assert from_panel["pause_source"] is None, (
        "атрибуция пережила снятие паузы: %r" % (from_panel,))
