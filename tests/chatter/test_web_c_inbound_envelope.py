# -*- coding: utf-8 -*-
"""Сторожа 1-7 спеки «ВЕБ, волна 2 / пара C» — КОНВЕРТ ВХОДЯЩЕГО.

Спека: `docs/superpowers/specs/2026-08-28-web-c-envelope-identity.md`, §2 и §9.

ПИСАНЫ ОТ ТЕКСТА СПЕКИ И ДО КОДА. Реализации арки автор этих сторожей не
видел, потому что её нет ([[jarvis-guards-not-by-the-plan-author]]): сторож,
написанный по коду, согласен с кодом по определению и молчит ровно там, где
код унаследовал неверное допущение плана.

ПОЧЕМУ ИМПОРТ ЛЕНИВЫЙ, А НЕ НА УРОВНЕ МОДУЛЯ. `chatter.core.inbound` сегодня
не существует. Импорт наверху сорвал бы СБОР файла целиком, и вместо семи
поимённо красных утверждений был бы один ImportError — то есть ноль
измеренных утверждений (грабля пары A, её же слова: «результат съеден чужим
ImportError»). Каждый сторож тянет модуль сам и краснеет СВОИМ отказом.
"""
from __future__ import annotations

import dataclasses
import importlib
import json
import random
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# §2.1 спеки, ДОСЛОВНО и ЛИТЕРАЛЬНО: список полей конверта в порядке спеки.
# Выведенный из dataclass список согласен с dataclass'ом по определению и
# промолчит ровно там, где поле забыли ([[jarvis-literal-lists-not-introspection]]).
ENVELOPE_FIELDS = (
    "channel",
    "external_id",
    "persona",
    "text",
    "ts",
    "display_name",
    "attachments_dropped",
)

# §2.2: чего в конверте НЕТ, с номером пункта — чтобы красное называло РЕШЕНИЕ,
# а не просто «лишнее поле».
FORBIDDEN_FIELDS = {
    "contact_id": "п.1 — только ПРОИЗВОДНОЕ свойство; поле рядом с тремя своими "
                  "частями это два числа на одну вещь, и меньшее гасит большее молча",
    "attachments": "п.2 — всегда пустой список это место, где фото теряется молча, "
                   "только теперь с видом порядка",
    "msg_id": "п.3 — единственный читатель это дедуп очереди волны 3; поле, которое "
              "все кладут и никто не читает, хранит неверное значение до дня, когда его прочтут",
    "ip": "п.5 — принадлежит ДВЕРИ и умирает на её пороге; конверт хранится 365 дней, "
          "а про IP в обещании баннера не сказано",
    "user_agent": "п.5 — то же: заголовки двери в том, что живёт год, расширяют обещание молча",
    "origin": "п.5 — то же",
    "role": "п.6 — конверт это ВХОДЯЩЕЕ, роль у него одна, и колонка для неё "
            "существовала бы только чтобы однажды принять неверное значение",
    "author": "п.6 — то же",
    "lead_id": "п.7 — склейка это СВЯЗЬ поверх двух личностей, а не поле внутри одной "
               "(решение владельца 24.08)",
}

# §3.1: литеральный реестр каналов. Оба обязаны приниматься; исчезновение любого
# из них — красное (встречная половина сторожа 4).
KNOWN_CHANNELS = ("telegram", "web")

# §2.1: `channel` берётся из реестра, значит канал ВНЕ реестра обязан быть отказом.
UNKNOWN_CHANNELS = ("instagram", "whatsapp", "Telegram", "TELEGRAM", "tg")


def _inbound():
    """Класс конверта или ГРОМКИЙ отказ с адресом спеки.

    Отдельной функцией, чтобы красное называло §2.1, а не строку теста."""
    try:
        mod = importlib.import_module("chatter.core.inbound")
    except ImportError as exc:
        raise AssertionError(
            "нет модуля `chatter/core/inbound.py` (%s). §2.1 спеки называет его "
            "дословно: замороженный датакласс `Inbound` живёт в ЯДРЕ, потому что "
            "о транспорте он не знает ничего и правило «ноль импортов Telethon в "
            "chatter/core/*» выполняется по построению." % exc) from exc
    cls = getattr(mod, "Inbound", None)
    assert cls is not None, (
        "модуль `chatter.core.inbound` есть, а класса `Inbound` в нём нет. Имя "
        "названо спекой §2.1 дословно — конверт это ТИП, на который смотрят "
        "очередь волны 3 и пара D, и переименовать его тихо нельзя.")
    assert dataclasses.is_dataclass(cls), (
        "`Inbound` не датакласс. §2.1: «замороженный датакласс» — от этого "
        "зависят и `dataclasses.asdict` (сторож 2, переживание json), и "
        "литеральная сверка полей (сторож 6).")
    return cls


def _env(**over):
    """Здоровый конверт. Все поля названы явно: конверт, собранный дефолтами,
    не проверяет ничего — он проверяет дефолты."""
    cls = _inbound()
    kw = dict(channel="telegram", external_id="8849893367", persona="volska",
              text="здравствуйте", ts=1756400000.0, display_name="Тест Т 1",
              attachments_dropped=0)
    kw.update(over)
    return cls(**kw)


# ═══ Сторож 1 ═══════════════════════════════════════════════════════════════

def test_guard01_contact_id_sobiraetsya_iz_tryoh_chastey():
    """Сторож 1: `contact_id` конверта — `"<channel>:<external_id>:<persona>"`.

    Это ЕДИНСТВЕННОЕ обещание конверта наружу: всё остальное дерево адресует
    контакт этой строкой. Если она собирается иначе (в другом порядке, с другим
    разделителем, без канала), то мигрированные базы и код разойдутся молча —
    и разойдутся на адресации, то есть на «сообщение не тому человеку»."""
    env = _env()
    assert env.contact_id == "telegram:8849893367:volska", (
        "конверт отдал contact_id %r, ждали 'telegram:8849893367:volska' "
        "(§3.1: ровно три сегмента, порядок канал-собеседник-персона)."
        % (env.contact_id,))


def test_guard01_contact_id_eto_svoystvo_a_ne_pole():
    """Сторож 1, вторая половина: `contact_id` НЕ поле (§2.2 п.1).

    Поле рядом с тремя своими частями — это два числа на одну вещь
    ([[jarvis-two-numbers-for-one-thing]]): меньшее гасит большее молча. Пин
    двойной, потому что «не поле» ломается двумя разными способами: его могут
    ОБЪЯВИТЬ полем (тогда его видно в `fields`) и его могут разрешить
    ПРИСВОИТЬ (тогда состояние конверта разъедется с его же частями)."""
    cls = _inbound()
    names = [f.name for f in dataclasses.fields(cls)]
    assert "contact_id" not in names, (
        "`contact_id` объявлен ПОЛЕМ конверта (поля: %r). §2.2 п.1: только "
        "производное свойство. Поле хранит копию того, что уже лежит в трёх "
        "соседних полях, и первая же правка одной из частей сделает копию "
        "неверной — молча." % (names,))
    env = _env()
    try:
        env.contact_id = "web:hijacked:volska"      # type: ignore[misc]
    except Exception:
        return
    raise AssertionError(
        "присвоение `env.contact_id` прошло без исключения: конверт не "
        "заморожен либо у contact_id есть сеттер. §2.1 говорит «замороженный», "
        "и это не стиль — конверт едет в очередь и в клиентский бэкап, значит "
        "его значение обязано быть тем же, что при сборке.")


# ═══ Сторож 2 ═══════════════════════════════════════════════════════════════

def test_guard02_konvert_perezhivaet_json_i_vozvrashchaetsya_RAVNYM():
    """Сторож 2: `json.loads(json.dumps(asdict(env)))` обратно в конструктор
    даёт РАВНЫЙ конверт (§2.2 п.4).

    Это механическая проверка того, что конверт — ДАННЫЕ, а не контекст. Без
    неё очередь волны 3 не строится вовсе: она обязана положить конверт на
    диск и поднять его после рестарта. Проверка именно на РАВЕНСТВО, а не на
    «не упало»: `json` съест и тот конверт, у которого `ts` из float стал
    строкой, — и разъедется он на сравнении времён, то есть далеко от места
    поломки."""
    cls = _inbound()
    env = _env()
    raw = json.dumps(dataclasses.asdict(env), ensure_ascii=False)
    back = cls(**json.loads(raw))
    assert back == env, (
        "конверт не пережил json: было %r, стало %r. §2.2 п.4 — это и есть "
        "механическая проверка «конверт это ДАННЫЕ»; без неё очередь волны 3 "
        "не построится." % (env, back))
    assert back.contact_id == env.contact_id, (
        "поля сравнялись, а производный contact_id — нет (%r против %r): "
        "значит равенство конвертов считается не по тем полям, из которых "
        "он собирается." % (back.contact_id, env.contact_id))


@pytest.mark.parametrize("field_name, payload", [
    ("text", "Store"),
    ("display_name", "transport"),
    ("external_id", "sqlite-connection"),
])
def test_guard02_nesserializuemoe_v_konverte_ne_prohodit(field_name, payload):
    """Сторож 2, пин на нессериализуемое (§2.2 п.4: ни `Store`, ни транспорт,
    ни соединение).

    Живой объект в конверте — это конверт, который нельзя ни сохранить, ни
    сравнить, ни переслать; узнаётся это в день, когда очередь попробует его
    записать, то есть в проде. Отказ обязан случиться РАНЬШЕ — при сборке либо
    при первом же `json.dumps`. Обе честные развилки принимаются; не
    принимается третья — «собрался и молча притворился строкой»."""
    cls = _inbound()

    class _Alive:
        """Двойник живого объекта, НЕ `MagicMock`: автомок истинен и у
        неаккуратной реализации сериализуется через `str()`
        ([[jarvis-magicmock-truthy-spins-the-loop]])."""

        def __init__(self, tag):
            self.tag = tag

    kw = dict(channel="telegram", external_id="123", persona="volska",
              text="привет", ts=1.0, display_name=None, attachments_dropped=0)
    kw[field_name] = _Alive(payload)
    try:
        env = cls(**kw)
    except Exception:
        return                          # честный отказ при сборке
    with pytest.raises(TypeError):
        json.dumps(dataclasses.asdict(env))


# ═══ Сторож 3 ═══════════════════════════════════════════════════════════════

@pytest.mark.parametrize("part", ["channel", "external_id", "persona"])
@pytest.mark.parametrize("bad, why", [
    ("", "пустой сегмент"),
    ("a:b", "двоеточие ВНУТРИ сегмента"),
    (":", "сегмент из одного двоеточия"),
])
def test_guard03_pustoy_segment_ili_dvoetochie_vnutri_eto_isklyuchenie(part, bad, why):
    """Сторож 3: пустой сегмент и двоеточие внутри любого из трёх — ИСКЛЮЧЕНИЕ
    при сборке, а не «почти конверт».

    Что ломается без этого. `contact_id` опознаётся ПО ЧИСЛУ СЕГМЕНТОВ (§3.1),
    и сегмент с двоеточием внутри превращает трёхсегментный id в
    четырёхсегментный — то есть в форму, которую разбор обязан отвергать
    (сторож 10). Собрать её конверт разрешить не может: тогда неразбираемый id
    родится ВНУТРИ нашего кода, а покраснеет через слой, у чужого разбора.
    Пустой сегмент хуже вдвойне: `"telegram::volska"` разбирается на три части
    без ошибки и адресует НИКОГО."""
    if part == "channel" and bad != "":
        pytest.skip("канал вне литерального реестра судит сторож 4; здесь — только пустота")
    with pytest.raises(ValueError):
        _env(**{part: bad})


# ═══ Сторож 4 ═══════════════════════════════════════════════════════════════

@pytest.mark.parametrize("channel", UNKNOWN_CHANNELS)
def test_guard04_kanal_vne_literalnogo_reestra_eto_otkaz(channel):
    """Сторож 4: `channel` вне литерального реестра — исключение.

    Реестр литеральный (§3.1) именно затем, чтобы канал нельзя было завести
    опечаткой. Опечатка в канале — это не «странная строка в базе»: голова
    входит в адресацию и в ключи идемпотентности, значит `"Telegram"` рядом с
    `"telegram"` дают ДВА контакта на одного человека, и оба выглядят живыми."""
    with pytest.raises(ValueError):
        _env(channel=channel)


@pytest.mark.parametrize("channel", KNOWN_CHANNELS)
def test_guard04_VSTRECHNAYA_polovina_reestr_ne_usoh(channel):
    """Сторож 4, ВСТРЕЧНАЯ ПОЛОВИНА: канал, пропавший из реестра, обязан
    краснеть, а не молча перестать проверяться.

    Без неё сторож выше зеленеет на реестре из ОДНОГО канала и даже на пустом:
    «всё, что не в реестре, — отказ» выполняется идеально, когда в реестре нет
    ничего. Тогда день, когда `web` выпал из реестра, наступает молча — и веб
    перестаёт принимать входящие ровно после мержа пары D."""
    env = _env(channel=channel)
    assert env.channel == channel
    assert env.contact_id.split(":")[0] == channel, (
        "канал %r принят, но в contact_id голова другая (%r): реестр и сборка "
        "смотрят в разные стороны." % (channel, env.contact_id))


# ═══ Сторож 5 ═══════════════════════════════════════════════════════════════

def test_guard05_display_name_None_i_pustaya_stroka_RAZLICHIMY():
    """Сторож 5: `display_name=None` и `display_name=""` — РАЗНЫЕ вещи.

    §2.1 говорит это прямо: «None ≠ ""». Смысл разный и он дорогой: `None` —
    «канал имени НЕ ДАЛ» (у веб-посетителя его нет), `""` — «канал дал пустое»
    (человек стёр имя в Telegram). Реализация, нормализующая одно в другое,
    делает эти два случая неразличимыми — и подпись карточки владельцу
    перестаёт отвечать на вопрос «мы не знаем» против «он не назвался»."""
    none_env = _env(display_name=None)
    empty_env = _env(display_name="")
    assert none_env.display_name is None, (
        "display_name=None превратился в %r: пустая строка и «имени нет» "
        "склеены." % (none_env.display_name,))
    assert empty_env.display_name == "", (
        "display_name='' превратился в %r: «канал дал пустое имя» подменено "
        "на «канал имени не дал»." % (empty_env.display_name,))
    assert none_env != empty_env, (
        "конверты с display_name=None и display_name='' сравнялись РАВНЫМИ — "
        "значит различие есть на входе и исчезает на сравнении, а сравнением "
        "живёт дедуп очереди волны 3.")


# ═══ Сторож 6 ═══════════════════════════════════════════════════════════════

def test_guard06_spisok_poley_LITERALNYY_v_obe_storony():
    """Сторож 6: состав полей конверта сверяется с ЛИТЕРАЛЬНЫМ списком §2.1 в
    ОБЕ стороны — лишнее красное, пропавшее красное.

    Односторонняя проверка бесполезна в обе стороны по отдельности: список
    «все объявленные поля на месте» зеленеет на конверте, куда дописали
    `attachments`; список «лишнего нет» зеленеет на конверте, из которого
    выпал `ts`. Конверт — это ДОГОВОР между ядром, дверью (пара D) и очередью
    (волна 3); договор, который можно тихо расширить, договором не является."""
    cls = _inbound()
    actual = tuple(f.name for f in dataclasses.fields(cls))
    missing = [n for n in ENVELOPE_FIELDS if n not in actual]
    extra = [n for n in actual if n not in ENVELOPE_FIELDS]
    assert not missing and not extra, (
        "состав полей конверта разошёлся со §2.1.\n"
        "  объявлено спекой: %r\n  найдено в коде:   %r\n"
        "  ПРОПАЛО: %r\n  ЛИШНЕЕ: %r"
        % (list(ENVELOPE_FIELDS), list(actual), missing, extra))
    assert actual == ENVELOPE_FIELDS, (
        "поля те же, но ПОРЯДОК другой: %r против %r. Порядок объявлен §2.1, и "
        "он значим: конструктор конверта зовут позиционно, а перестановка двух "
        "соседних str-полей местами не упадёт нигде — она просто положит имя "
        "лида в текст." % (list(actual), list(ENVELOPE_FIELDS)))


@pytest.mark.parametrize("name", sorted(FORBIDDEN_FIELDS))
def test_guard06_zapreshchyonnogo_polya_v_konverte_NET(name):
    """Сторож 6, отдельный пин на КАЖДОЕ поле, которого в конверте нет по
    решению §2.2 — с номером пункта в красном.

    Отдельным параметром на пункт, а не одним assert'ом со списком: «в конверт
    просочилось `attachments`» и «в конверт просочился `ip`» — разные диагнозы
    с разной ценой (первое теряет фото, второе расширяет обещание о хранении
    365 дней на данные, о которых в баннере не сказано)."""
    cls = _inbound()
    names = {f.name for f in dataclasses.fields(cls)}
    assert name not in names, (
        "в конверте появилось поле %r. §2.2 %s" % (name, FORBIDDEN_FIELDS[name]))


# ═══ Сторож 7 ═══════════════════════════════════════════════════════════════

def test_guard07_fayl_bez_teksta_daet_pustoy_text_i_schyot_poteri():
    """Сторож 7: входящее с файлом и без подписи даёт `text == ""` и
    `attachments_dropped >= 1`.

    §2.3: конверт делает пустоту ОТЛИЧИМОЙ — «пусто, потому что ничего»
    против «пусто, потому что пришёл файл, который мы не берём». Имя поля
    считает ПОТЕРЮ, а не притворяется поддержкой: `has_media: bool` через
    полгода прочтут как «медиа поддерживается»."""
    lost = _env(text="", attachments_dropped=1)
    nothing = _env(text="", attachments_dropped=0)
    assert lost.text == "" and lost.attachments_dropped == 1
    assert lost != nothing, (
        "конверт «пришёл файл, текста нет» сравнялся с конвертом «не пришло "
        "ничего»: ровно та неразличимость, ради снятия которой §2.3 и вводит "
        "поле. Считать потерю станет нечем, и медиа-арка не узнает своего "
        "размера.")


def test_guard07_pered_LIDOM_povedenie_ne_menyaetsya_ni_na_bayt(tmp_path):
    """Сторож 7, вторая половина и ГЛАВНАЯ: лид не получает НИЧЕГО нового.

    §2.3 говорит это красным: «Поведение перед ЛИДОМ не меняется ни на байт.
    Бот молчал — молчит.» Причина названа там же: менять то, что видит лид
    живой клиентки, в одной транзакции с миграцией её базы — это два живых
    радиуса в одном мерже.

    Меряем ИСХОД, а не текст: пустой ход через `process_batch` обязан оставить
    транспорт без единой отправки. Сторож переживает арку по построению — он
    не знает ни о конверте, ни о том, где он включён."""
    from chatter.config.loader import load_config
    from chatter.core.brain import Brain
    from chatter.core.llm import FakeLLM
    from chatter.run import Deps, process_batch
    from chatter.storage.db import Store
    from chatter.transport.fake import FakeConsoleTransport

    cfg = load_config(REPO_ROOT / "chatter" / "clients", "demo")
    store = Store(str(tmp_path / "guard07.db"))
    try:
        deps = Deps(cfg=cfg, store=store,
                    brain=Brain(FakeLLM(scripted=["этого лид видеть не должен"]), cfg),
                    rng=random.Random(0), clock=lambda: 1000.0, sleep=lambda _s: None)
        transport = FakeConsoleTransport(preload=[], echo=False)
        process_batch("telegram:111:demo", [""], transport, deps)
        assert transport.sent == [], (
            "на входящем с ПУСТЫМ текстом (фото без подписи) лид получил %r. "
            "§2.3: поведение перед лидом не меняется ни на байт — потеря "
            "только СЧИТАЕТСЯ. Честный отказ словами строит пара D в вебе, "
            "где живого клиента нет вовсе." % (transport.sent,))
    finally:
        store.close()
