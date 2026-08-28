# -*- coding: utf-8 -*-
"""Сторожа арки «личность контакта: один разбор вместо восьми» (ВЕБ, волна 1, пара A).

Писаны ОТ ТЕКСТА СПЕКИ и ДО кода: модуля `chatter.core.contact_ref` на момент
написания не существует, реализацию автор этих сторожей не видел. Поэтому файл
обязан быть КРАСНЫМ на сегодняшнем дереве — целиком, уже на сборе, потому что
импорт нового модуля стоит на уровне файла НАМЕРЕННО. Прятать его в try/except
или в `pytest.importorskip` нельзя: сторож, который «пропускается», пока кода
нет, зелёный по построению, а зелёный по построению сторож — это то же враньё,
что и отсутствие сторожа.

Здесь сторожа §5 спеки номер 1–7 и 10. Сторожа 8 и 9 (по AST) вынесены в
`guards_a_no_raw_split.py`: они сканируют дерево и НЕ зависят от нового модуля,
а падение импорта в этом файле спрятало бы их результат.

Что арка защищает. Сегодня `contact_id` разбирается в восьми местах руками
(`contact_id.split(":", 1)[0]`). Шесть из восьми на форме, которой не знают,
возвращают правдоподобный мусор — строку `"instagram"` там, где имелся в виду
собеседник, — и это доедет до клиента. Два падают громко. Арка сводит все
восемь к одной fail-closed функции. Сторожа ниже пинят ДВЕ вещи разом:
что новая функция ведёт себя fail-closed, и что на СЕГОДНЯШНЕЙ форме каждое
из восьми мест отдаёт ровно то же, что отдавало до правки.

ТИП ИСКЛЮЧЕНИЯ. Ждём `ValueError` — и вот почему именно его. Спека (§2.2)
требует «исключение с названным contact_id», но класса не называет; сторож
обязан оставить реализатору свободу, которую спека оставила, и при этом не
пропустить дефект. `ValueError` — стандартный питоновский контракт «тип
аргумента верный, значение — нет», ровно наш случай: на вход подали строку,
и она строка, просто форма чужая. Собственный класс-наследник `ValueError`
(если реализатор захочет его завести) проходит `isinstance` и сторожа не
ломает, а вот `return ""`, `return None` или `KeyError` из недр — ломает.
Чтобы `ValueError` не выродился в «поймали ЛЮБОЙ ValueError, в том числе тот,
что сам `int()` бросил из недр», сторож 7 отдельно требует, чтобы в тексте
отказа стоял ПОЛНЫЙ `contact_id`, — сообщение `int()` его не содержит
(там только голова), и подмена ловится.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from chatter.core.contact_ref import peer_of, slug_of, telegram_peer_of

import chatter.notify.control_bot as control_bot
import chatter.run as chatter_run
import chatter.telethon_run as telethon_run
import app.services.tamapi_metrics as tamapi_metrics
from chatter.config.loader import ControlConfig
from chatter.notify.base import CardHandle  # noqa: F401  (форма ответа notify)


# Форма, на которой живут ОБЕ живые клиентки прямо сейчас: "<peer_id>:<slug>".
TODAY = "12345:volska"
TODAY_PEER_STR = "12345"
TODAY_PEER_INT = 12345
TODAY_SLUG = "volska"


def _assert_names_contact_id(exc: BaseException, contact_id: str) -> None:
    """Отказ обязан называть ВЕСЬ contact_id, а не только тот кусок, о который
    споткнулся.

    Без этого условия сторож принял бы `ValueError`, прилетевший из недр
    `int()` («invalid literal for int() with base 10: 'abc'»), за честный
    fail-closed отказ — а это ровно тот дефект, который спека §5.7 называет
    отдельным пунктом. Владелец, читающий лог, по такому сообщению не узнает,
    ЧЕЙ диалог отвалился."""
    text = str(exc)
    assert contact_id in text, (
        f"ждали отказ, называющий весь contact_id {contact_id!r}; получили "
        f"текст {text!r}. Такой отказ не даёт узнать, чей диалог отвалился: "
        f"в логе останется обломок формы вместо адреса собеседника."
    )


# --------------------------------------------------------------------------
# Сторож 1-3: сегодняшняя форма разбирается БАЙТ-В-БАЙТ как раньше.
# Это не «проверка тривиального»: вся арка продаётся владельцу обещанием
# «поведение на сегодняшнем формате остаётся прежним, это и есть приёмка»
# (спека §0/§2.3). Без этих трёх пинов обещание ничем не подтверждено, и
# любая правка разбора уедет на живых клиенток непроверенной.
# --------------------------------------------------------------------------

def test_todays_two_segment_form_still_yields_the_bare_peer():
    """`peer_of` на сегодняшней форме отдаёт голову — ту же строку, что
    возвращал ручной `split` во всех восьми местах. Разойдись он здесь — обе
    живые клиентки начали бы звать лида не тем адресом в тот же час."""
    got = peer_of(TODAY)
    assert got == TODAY_PEER_STR, (
        f"peer_of({TODAY!r}) ждали {TODAY_PEER_STR!r}, получили {got!r}; "
        f"это отличие от сегодняшнего поведения восьми мест — карточки уедут "
        f"владельцу с чужим адресом собеседника."
    )


def test_telegram_peer_of_returns_an_int_not_a_string():
    """`telegram_peer_of` обязан отдать ЧИСЛО.

    Два громких места (`telethon_run.py:1216` и `:1580`) сегодня делают
    `int(...)` руками и передают результат в `client.get_entity`. Отдай функция
    строку — Telethon получил бы не тот тип, и это молчаливо развалило бы
    резолв entity: карточка ушла бы с голым id вместо имени, а причина осела
    бы в логе. Отдельная проверка `type is int` нужна потому, что `"12345" ==
    12345` ложно, а вот `bool`/`float` мимо `==` не всегда проскочат."""
    got = telegram_peer_of(TODAY)
    assert got == TODAY_PEER_INT and type(got) is int, (
        f"telegram_peer_of({TODAY!r}) ждали int {TODAY_PEER_INT!r}, получили "
        f"{got!r} типа {type(got).__name__}; get_entity на не-int молча не "
        f"разрешит собеседника, и владелец увидит число вместо имени."
    )


def test_slug_of_returns_the_persona_tail():
    """`slug_of` отдаёт хвост — слуг персоны. Он существует ровно затем, чтобы
    `rsplit` не расползался по коду отдельной копией в каждом новом месте."""
    got = slug_of(TODAY)
    assert got == TODAY_SLUG, (
        f"slug_of({TODAY!r}) ждали {TODAY_SLUG!r}, получили {got!r}; по слугу "
        f"выбирается персона (язык карточки и имя), промах здесь = ответ "
        f"владельцу на чужом языке."
    )


# --------------------------------------------------------------------------
# Сторож 4: ОБРАТИМОСТЬ. Что приняли — то обязаны разобрать без потерь.
#
# Сегодняшняя форма — СТРОГО два сегмента, ровно одно двоеточие. Это не
# рассуждение, а замер: `contact_id` собирается ровно в трёх местах и всегда
# одинаково — `f"{sender_id}:{persona_slug}"` (telethon_run.py:1702, :1785,
# :1855), а все слуги реестра (volska, demo, demo2, yarina) двоеточий не
# содержат. Формы со слугом-с-двоеточием в природе нет.
#
# Поэтому сторож проверяет не «где режем», а свойство сильнее: РАЗБОР
# ОБРАТИМ. Он ловит реализацию, которая приняла лишний сегмент и молча
# потеряла середину, — а именно такая потеря и есть тихий неверный ответ,
# ради которого вся арка. Сторож 5 говорит о другом (явно чужой канал);
# здесь речь о том, что ПРИНЯТОЕ разобрано без остатка.
# --------------------------------------------------------------------------

# Литеральный список форм, которые три места сборки реально производят. Не
# выведенный обходом кода: выведенный согласен с кодом по определению и
# промолчит там, где код забыл ([[jarvis-literal-lists-not-introspection]]).
# Отрицательная голова — не выдумка: chat_id групп и каналов в Telegram
# отрицательный, и `f"{sender_id}:{slug}"` соберёт её так же.
CANONICAL_FORMS = ("12345:volska", "777:demo", "777:demo2",
                   "12345:yarina", "-1001234567890:demo")

# Формы «на грани»: принять их или отвергнуть — решение реализации, сторож
# не навязывает. Но ЧТО БЫ она ни решила, потерять кусок она не имеет права.
BORDERLINE_FORMS = ("0:volska", "12345:VOLSKA", "12345:слуг-которого-нет")


def test_every_accepted_form_reassembles_into_the_original():
    """Инвариант обратимости: `peer_of(x) + ":" + slug_of(x) == x`.

    Что сломается без него. Реализация вида «`split(":")`, голова `[0]`, хвост
    `[-1]`» на `"12345:abc:volska"` вернёт `"12345"` и `"volska"` — оба куска
    выглядят правдоподобно, серединка `"abc"` исчезнет молча, и ни один
    сторож на равенство отдельных значений этого не заметит. Обратимость
    замечает: собранное обратно не совпадёт с исходным.

    Форму, которую реализация ОТВЕРГЛА, инвариант не касается — про отказы
    сторожа 5 и 6. Но чтобы цикл не выродился в зелёный по построению
    («всё отвергли — проверять нечего»), канонические формы обязаны быть
    приняты, и это проверяется отдельно ниже."""
    accepted = []
    for form in CANONICAL_FORMS + BORDERLINE_FORMS:
        try:
            head, tail = peer_of(form), slug_of(form)
        except ValueError:
            continue          # отвергнута — законный исход, судят сторожа 5/6
        accepted.append(form)
        rebuilt = f"{head}:{tail}"
        assert rebuilt == form, (
            f"разбор {form!r} НЕ обратим: peer_of={head!r}, slug_of={tail!r}, "
            f"собранное обратно {rebuilt!r} != исходного. Значит часть "
            f"contact_id потерялась при разборе — оба куска выглядят "
            f"правдоподобно, а адресуют не того собеседника, и заметить это "
            f"по отдельным значениям невозможно."
        )
    missing = [f for f in CANONICAL_FORMS if f not in accepted]
    assert not missing, (
        f"канонические формы {missing!r} были ОТВЕРГНУТЫ; их собирают три "
        f"места раннера (telethon_run.py:1702, :1785, :1855), отказ на них "
        f"остановит обеих живых клиенток. Заодно: отвергнув всё, цикл выше "
        f"стал бы зелёным, не проверив ничего."
    )


@pytest.mark.parametrize("fn_name", ["peer_of", "telegram_peer_of", "slug_of"])
def test_a_three_segment_form_is_rejected_not_silently_shortened(fn_name):
    """Встречная половина к обратимости: `"12345:my:slug"` обязан быть ОТВЕРГНУТ.

    Голова здесь числовая, и опознание «по числовой голове» приняло бы эту
    форму за свою. Опознаём по ЧИСЛУ СЕГМЕНТОВ: три — не наша форма, точка.
    Разница не академическая — завтрашний формат
    `"<channel>:<external_id>:<persona>"` принесёт числовые external_id, и
    «числовая голова = телеграм» начнёт тихо принимать чужую форму ровно
    тогда, когда цена ошибки максимальна.

    Пара к сторожу выше: тот запрещает принять и потерять, этот — принять
    вообще. Без него реализация может честно вернуть `"12345"` и `"my:slug"`
    (обратимо!) и всё равно быть неправой: такого contact_id никто не
    собирает, значит форма пришла ниоткуда, и угадывать её нельзя."""
    fn = {"peer_of": peer_of, "telegram_peer_of": telegram_peer_of,
          "slug_of": slug_of}[fn_name]
    three = "12345:my:slug"
    with pytest.raises(ValueError) as ei:
        result = fn(three)
        pytest.fail(
            f"{fn_name}({three!r}) вернул {result!r} вместо отказа; сегодня "
            f"такую форму не собирает никто (три места сборки дают ровно два "
            f"сегмента), а завтра её принесёт следующая арка — и принятая "
            f"заранее, она разойдётся с ней молча, уже на живом."
        )
    _assert_names_contact_id(ei.value, three)


# --------------------------------------------------------------------------
# Сторож 5-6: fail-closed. Форму, которой не знаем, НЕ УГАДЫВАЕМ.
# Это сердце арки: шесть молчаливых мест сегодня возвращают правдоподобный
# мусор, и именно мусор доезжает до клиента. Падение видно в тот же день,
# строка "instagram" вместо собеседника — нет.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("fn_name", ["peer_of", "telegram_peer_of", "slug_of"])
def test_an_unknown_form_raises_instead_of_returning_plausible_garbage(fn_name):
    """`"instagram:abc:volska"` — форма завтрашней арки, сегодня НЕ наша.

    Спека §2.5: трёхсегментную форму функция пока отвергает, потому что
    принять её заранее — значит завести второе поведение, которого ничто не
    проверяет, и обнаружить его расхождение с будущей аркой уже на живом.
    Форму опознаём по ЧИСЛУ СЕГМЕНТОВ, а не по виду головы: сегодняшний
    contact_id — строго два сегмента (замер трёх мест сборки в
    telethon_run.py:1702, :1785, :1855). Опознание «по голове» здесь тоже
    покраснело бы, но оно приняло бы `"12345:abc:volska"` — форму завтрашней
    арки с числовым external_id, — и разошлось бы с ней молча; счёт сегментов
    отвергает обе. Все три функции обязаны отказать одинаково: угадать хвост
    чужой формы — тот же класс дефекта, что угадать голову."""
    fn = {"peer_of": peer_of, "telegram_peer_of": telegram_peer_of,
          "slug_of": slug_of}[fn_name]
    unknown = "instagram:abc:volska"
    with pytest.raises(ValueError) as ei:
        result = fn(unknown)
        pytest.fail(
            f"{fn_name}({unknown!r}) вернул {result!r} вместо отказа; ровно так "
            f"шесть молчаливых мест и отдают правдоподобный мусор дальше — он "
            f"доезжает до клиента и никем не замечается."
        )
    _assert_names_contact_id(ei.value, unknown)


@pytest.mark.parametrize("bad", ["", "12345", "volska", ":", "   "])
@pytest.mark.parametrize("fn_name", ["peer_of", "telegram_peer_of", "slug_of"])
def test_empty_or_colonless_input_raises(fn_name, bad):
    """Пустая строка и строка без двоеточия — не форма вовсе.

    Сегодняшний `split(":", 1)[0]` на `""` отдаёт `""`, а на `"volska"` —
    `"volska"`: обе строки выглядят как ответ, обе бессмысленны как адрес
    собеседника. Пустой contact_id доезжает сюда реально — `Card(contact_id="")`
    строится в `telethon_run._notify_owner_notice`, и такой «ответ» ушёл бы в
    ссылку `tg://user?id=`."""
    fn = {"peer_of": peer_of, "telegram_peer_of": telegram_peer_of,
          "slug_of": slug_of}[fn_name]
    with pytest.raises(ValueError) as ei:
        result = fn(bad)
        pytest.fail(
            f"{fn_name}({bad!r}) вернул {result!r} вместо отказа; строка без "
            f"формы, принятая за ответ, уедет в ссылку на собеседника и "
            f"владелец тапнет в пустоту."
        )
    _assert_names_contact_id(ei.value, bad)


# --------------------------------------------------------------------------
# Сторож 7: отказ по нечисловой голове — НАШ, а не из недр int().
# --------------------------------------------------------------------------

def test_telegram_peer_of_refuses_by_itself_not_by_leaking_int_valueerror():
    """`telegram_peer_of("abc:volska")` обязан отказать САМ и назвать contact_id.

    Отличие не косметическое. `int("abc")` бросает
    `ValueError: invalid literal for int() with base 10: 'abc'` — тип тот же,
    а сообщение не содержит ни имени персоны, ни того, чей это диалог. Такой
    трейсбек в логе живой клиентки не даёт починить НИЧЕГО: видно, что что-то
    не число, и не видно, у кого. Форма проверяется ДО `int()`, поэтому
    сообщение обязано нести весь `contact_id`."""
    bad = "abc:volska"
    with pytest.raises(ValueError) as ei:
        telegram_peer_of(bad)
    text = str(ei.value)
    _assert_names_contact_id(ei.value, bad)
    assert "invalid literal for int()" not in text, (
        f"telegram_peer_of({bad!r}) пропустил наружу ValueError из недр int(): "
        f"{text!r}. Отказ обязан быть НАШ и проверен ДО int() — иначе в логе "
        f"останется обломок 'abc' без указания, чей это диалог."
    )


# ==========================================================================
# Сторож 10: каждое из ВОСЬМИ мест на сегодняшнем contact_id отдаёт ровно то
# же, что отдавало до правки. ПОИМЁННО, а не «мы всё заменили».
#
# Почему поимённо. «Заменили везде» — это утверждение о тексте, и мутационный
# гейт его не проверяет: восемь вызовов новой функции, один из которых зовёт
# `slug_of` вместо `peer_of`, читаются как «всё заменено» и при этом ломают
# одно место молча. Поэтому каждый сторож ниже дёргает НАЗВАННУЮ функцию
# названного файла и смотрит, какой ИМЕННО peer она передала дальше.
#
# Наблюдаемое выбрано так, чтобы не зависеть от текста реализации: во всех
# шести местах run.py/telethon_run.py разобранная голова уходит в
# `contact_link(user_id=...)`/`client.get_entity(...)` — то есть в адрес
# собеседника, ради которого разбор и делается. Именно это значение (и его
# ТИП) мы и снимаем.
# ==========================================================================

class _Rec:
    """Записыватель аргумента `user_id`, которым место адресует собеседника."""

    def __init__(self):
        self.user_ids = []

    def __call__(self, *, username=None, user_id=None):
        self.user_ids.append(user_id)
        return f"tg://user?id={user_id}"


class _SilentNotifier:
    """Notifier, который «не доставил». Возврат None — легальный путь во всех
    четырёх местах run.py (они его логируют и выходят), поэтому место успевает
    построить карточку (а значит и адрес) и не тащит за собой сеть."""

    def __init__(self):
        self.cards = []

    def notify(self, card):
        self.cards.append(card)
        return None

    def update_card(self, handle, card):
        self.cards.append(card)
        return False

    @property
    def has_buttons(self):
        return False


def _deps(notifier, *, store=None):
    settings = SimpleNamespace(language="ru", persona_name="Аня")
    return SimpleNamespace(
        cfg=SimpleNamespace(settings=settings),
        store=store if store is not None else _FakeStore(),
        notifier=notifier,
        control=ControlConfig(),
        escalation_card=None,
    )


class _FakeStore:
    """Ровно те методы Store, которых касаются четыре места run.py. Настоящий
    Store здесь не нужен и вреден: сторож меряет РАЗБОР contact_id, а не БД
    (и, по DEV-78, не имеет права оставлять осадок в `state/`)."""

    def history(self, contact_id):
        return []

    def get_runtime_flag(self, key):
        return None

    def get_runtime_flag_ts(self, key):
        return None

    def set_runtime_flag(self, key, value, ts=None):
        return None

    def add_card(self, **kwargs):
        return None


def test_site_control_bot_peer_of_returns_todays_head():
    """`chatter/notify/control_bot.py:52` — `_peer_of`. Самое простое из восьми
    мест: чистая функция, наблюдаемое — её возврат. Отсюда строится ссылка на
    лида в карточках пульта; промах = владелец тапает в чужой диалог."""
    got = control_bot._peer_of(TODAY)
    assert got == TODAY_PEER_STR, (
        f"control_bot._peer_of({TODAY!r}) ждали {TODAY_PEER_STR!r}, получили "
        f"{got!r}; ссылка на лида в карточке пульта уедет не туда."
    )


def test_site_tamapi_metrics_peer_falls_back_to_todays_head():
    """`app/services/tamapi_metrics.py:74` — `_peer`. Голый id здесь — фолбэк,
    когда раннер не запомнил имени. Подаём пустое имя, чтобы фолбэк сработал:
    именно он и режет contact_id. Промах = в дашборде вместо лида окажется
    строка, по которой оператор не возобновит диалог."""
    got = tamapi_metrics._peer(TODAY, "")
    assert got == TODAY_PEER_STR, (
        f"tamapi_metrics._peer({TODAY!r}, '') ждали {TODAY_PEER_STR!r}, "
        f"получили {got!r}; карточка лида в дашборде назовётся не тем."
    )


def test_site_run_post_invoice_card_links_to_todays_head(monkeypatch):
    """`chatter/run.py:287` — `_post_invoice_card`. Денежная карточка владельцу.
    Наблюдаемое — `user_id`, которым она адресует лида."""
    rec = _Rec()
    monkeypatch.setattr(chatter_run, "contact_link", rec)
    note = SimpleNamespace(kind="invoice_awaiting_owner", invoice_id="INV-1",
                           reasons=("нет реквизитов",))
    chatter_run._post_invoice_card(_deps(_SilentNotifier()), TODAY, note, now=1000.0)
    assert rec.user_ids == [TODAY_PEER_STR], (
        f"_post_invoice_card адресовал лида {rec.user_ids!r}, ждали "
        f"[{TODAY_PEER_STR!r}]; ссылка в карточке счёта ведёт не к тому "
        f"собеседнику — владелец обсудит деньги с чужим человеком."
    )


def test_site_run_post_escalation_card_links_to_todays_head(monkeypatch):
    """`chatter/run.py:719` — `_post_escalation_card`, ветка БЕЗ раннера
    (`deps.escalation_card is None`): именно она режет contact_id руками.
    Наблюдаемое — `user_id` в ссылке карточки эскалации."""
    rec = _Rec()
    monkeypatch.setattr(chatter_run, "contact_link", rec)
    chatter_run._post_escalation_card(
        _deps(_SilentNotifier()), TODAY, det=None, cr=None, now=1000.0)
    assert rec.user_ids and set(rec.user_ids) == {TODAY_PEER_STR}, (
        f"_post_escalation_card адресовал лида {rec.user_ids!r}, ждали только "
        f"{TODAY_PEER_STR!r}; карточка горячего лида уедет с чужой ссылкой, и "
        f"владелец позвонит не тому."
    )


def test_site_run_note_profile_miss_links_to_todays_head(monkeypatch):
    """`chatter/run.py:815` — `_note_profile_miss`, алерт про замёрзшую память.

    `note_profile_miss` подменён: он ходит в БД, а мерить мы приехали разбор
    contact_id, не серию. Возвращаем сразу порог, чтобы дойти до алерта."""
    rec = _Rec()
    monkeypatch.setattr(chatter_run, "contact_link", rec)
    monkeypatch.setattr(chatter_run, "note_profile_miss",
                        lambda store, contact_id, now: ControlConfig().profile_stale_threshold)

    class _Store(_FakeStore):
        def get_runtime_flag(self, key):
            return "0"

    chatter_run._note_profile_miss(
        _deps(_SilentNotifier(), store=_Store()), TODAY, now=1000.0, why="нет профиля")
    assert rec.user_ids and set(rec.user_ids) == {TODAY_PEER_STR}, (
        f"_note_profile_miss адресовал лида {rec.user_ids!r}, ждали только "
        f"{TODAY_PEER_STR!r}; алерт «бот помнит позавчерашнее» назовёт не того "
        f"лида, и владелец пойдёт чинить чужой диалог."
    )


def test_site_run_stale_card_notice_links_to_todays_head(monkeypatch):
    """`chatter/run.py:853` — `_maybe_stale_card_notice`. Предупреждение
    «карточка могла устареть». Наблюдаемое — тот же `user_id`."""
    rec = _Rec()
    monkeypatch.setattr(chatter_run, "contact_link", rec)
    chatter_run._maybe_stale_card_notice(
        _deps(_SilentNotifier()), TODAY, now=1000.0, card_posted=True)
    assert rec.user_ids and set(rec.user_ids) == {TODAY_PEER_STR}, (
        f"_maybe_stale_card_notice адресовал лида {rec.user_ids!r}, ждали "
        f"только {TODAY_PEER_STR!r}; предупреждение про устаревшую карточку "
        f"укажет на чужой диалог."
    )


class _RecordingClient:
    """Telethon-клиент, который только запоминает, КОГО у него спросили, и
    отказывается резолвить. Оба места telethon_run переживают отказ (у них есть
    фолбэк на голый id) — значит функция дойдёт до конца, а мы увидим ЗНАЧЕНИЕ
    и ТИП запрошенного peer. Тип здесь и есть половина сторожа: сегодня оба
    места делают `int(...)`, и подмена на строку молча убьёт резолв имени."""

    def __init__(self):
        self.asked = []

    def get_entity(self, peer):
        self.asked.append(peer)
        return None                       # не корутина -> run_coroutine_threadsafe бросит


class _AsyncRecordingClient(_RecordingClient):
    async def get_entity(self, peer):     # type: ignore[override]
        self.asked.append(peer)
        raise RuntimeError("peer не резолвится — фолбэк на голый id")


def _fake_runner(client, store):
    """`TelethonRunner` без сети и без логина: собираем экземпляр в обход
    `__init__` и подставляем ровно те поля, которых касаются два места. Живой
    раннер поднимать нельзя — он лезет в Telegram, а сторож меряет разбор."""
    runner = object.__new__(telethon_run.TelethonRunner)
    settings = SimpleNamespace(language="ru", persona_name="Аня",
                               control=ControlConfig())
    bundle = SimpleNamespace(cfg=SimpleNamespace(settings=settings),
                             deps=SimpleNamespace(store=store))
    runner.personas = {TODAY_SLUG: bundle}
    runner.primary_slug = TODAY_SLUG
    runner.client = client
    runner.loop = None
    return runner


def test_site_telethon_build_escalation_card_asks_for_todays_int_peer():
    """`chatter/telethon_run.py:1216` — `build_escalation_card`. Одно из ДВУХ
    громких мест: сегодня `int(contact_id.split(":", 1)[0])`. Наблюдаемое —
    какой peer уехал в `client.get_entity`, и что это `int`."""
    client = _RecordingClient()
    runner = _fake_runner(client, _FakeStore())
    runner.build_escalation_card(TODAY, "хочет смету", "ключевое слово", [])
    assert client.asked and client.asked[0] == TODAY_PEER_INT \
        and type(client.asked[0]) is int, (
        f"build_escalation_card спросил entity про {client.asked!r}, ждали "
        f"int {TODAY_PEER_INT!r}; не тот peer (или строка вместо int) = имя "
        f"лида не разрешится, и владелец увидит в карточке голое число."
    )


def test_site_telethon_render_status_asks_for_todays_int_peer():
    """`chatter/telethon_run.py:1580` — `render_status` (`/status`). Второе
    громкое место, форма другая: `int(row["contact_id"].split(":")[0])` — без
    `maxsplit`, через словарь строки БД. Отдельный сторож нужен именно потому,
    что форма другая: правка, причесавшая только `contact_id.split`, это место
    пропустит, а `/status` — единственный способ владельца понять, почему бот
    молчит."""
    client = _AsyncRecordingClient()

    class _StatusStore(_FakeStore):
        def muted_contacts(self):
            return [{"contact_id": TODAY, "pause_until": None,
                     "pause_source": "human_takeover", "pause_detail": None,
                     "pause_msg_id": None, "paused_at": 900.0,
                     "last_human_out_ts": 900.0}]

        def issue_status_index(self, contact_ids, now):
            return None

        def count_events(self, kind, since_ts):
            return 0

    runner = _fake_runner(client, _StatusStore())
    asyncio.run(runner.render_status())
    assert client.asked and client.asked[0] == TODAY_PEER_INT \
        and type(client.asked[0]) is int, (
        f"render_status спросил entity про {client.asked!r}, ждали int "
        f"{TODAY_PEER_INT!r}; /status напечатает не того собеседника, а именно "
        f"по его номеру владелец наберёт /resume N."
    )
