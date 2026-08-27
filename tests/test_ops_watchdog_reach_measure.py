# -*- coding: utf-8 -*-
"""§5.1, слой ЗАМЕРА: чем доказывается целость провода и что делать с
проводом без адреса.

ПОЧЕМУ ОТДЕЛЬНЫЙ ФАЙЛ. Соседний `test_ops_watchdog_reachability.py` кормит
готовый снимок прямо в `probe_all()` и стережёт СОСТАВ вердиктов. Два решения
спеки лежат НИЖЕ этой точки и им не покрыты вовсе:

  А. `_reach_one` — одиночный замер. ЛЮБОЙ HTTP-ответ означает, что TLS
     состоялся и с той стороны ответил настоящий хост, поэтому 401, 403, 405 и
     даже 500 — ЗЕЛЁНОЕ. Подменённый сертификат рвёт связь ДО кода ответа.
     Это не поблажка, а то, что делает пробу бесплатной: иначе провод `llm_api`
     требовал бы УСПЕШНОГО платного вызова 2880 раз в сутки.
  Б. `_reachability_snapshot` — строитель. Адрес R2 живёт в `.env`
     (`R2_ENDPOINT`), литералом в коде его нет. Нет переменной → пробы этого
     провода НЕТ ВОВСЕ: не красная и не зелёная. Красная была бы вечной лампой
     на машине, где R2 просто не настроен, то есть фоном
     ([[jarvis-gate-mutates-the-deploy-tree]]).

РАЗЛИЧИЕ ПРИЧИН — ТРЕТЬЕ, и оно про человека. `tls` и `no_response` обязаны
быть РАЗНЫМИ словами. 26.08 отказ выглядел как успешное TCP-рукопожатие с
немедленным разрывом: одно слово на две беды отправило бы владельца
перезагружать роутер вместо поиска подмены. Красное, называющее не ту причину,
дороже отсутствующего — оно тратит то самое окно в 30 минут.

⚠️ ЗДЕСЬ НЕ ПРОВЕРЯЕТСЯ `terms[<провод>].ok` ИЗ ВЕРДИКТА. По решению автора
реализации это ОБДУМАННЫЙ вердикт после `DEBOUNCE` неудач подряд (рядом лежат
сырой `ok_now` и `fail_streak`): мгновенный вердикт означал бы, что
пятисекундная сетевая рябь будит человека Pushover'ом три часа, а такого
сторожа через неделю выключают. Поэтому сторожа ниже стоят на СЫРОМ замере
(`_reach_one`) и на СОСТАВЕ термов (`_reachability_snapshot`) — то есть ровно
на том, что от дебаунса не зависит.

СЕТЬ НЕ ТРОГАЕТСЯ. Обе функции принимают инъекцию; настоящий
`urllib.request.urlopen` в двух тестах подменён на взрыв — чтобы «сеть не
трогается» было ЗАМЕРОМ, а не обещанием.

Сторожа писались ОТ ТРЕБОВАНИЯ, реализации автор не видел. В этом дереве её
нет — файл ОБЯЗАН быть красным целиком.
"""
from __future__ import annotations

import email.message
import importlib.util
import inspect
import io
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_SPEC = importlib.util.spec_from_file_location(
    "ops_watchdog_reach_measure", ROOT / "scripts" / "ops_watchdog.py")
ow = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = ow
_SPEC.loader.exec_module(ow)


# ── контракт, литералами ───────────────────────────────────────────────────
REASON_TLS = "tls"
REASON_NO_RESPONSE = "no_response"

# Провода, чей адрес зашит литералом: они есть ВСЕГДА, при любом `.env`.
ADDRESSLESS_WIRES = ("tg_api", "llm_api")
# Провод, чей адрес приезжает из `.env`, и имя переменной.
ADDRESSED_WIRE = "r2"
ADDRESS_VAR = "R2_ENDPOINT"
R2_HOST = "abc123.r2cloudstorage.example"
R2_URL = "https://%s" % R2_HOST

ENV_WITH_R2 = (
    "TELEGRAM_BOT_TOKEN=1234567890:AAHfakefakefake\n"
    "ANTHROPIC_API_KEY=sk-ant-fake\n"
    "%s=%s\n" % (ADDRESS_VAR, R2_URL)
)
ENV_WITHOUT_R2 = (
    "TELEGRAM_BOT_TOKEN=1234567890:AAHfakefakefake\n"
    "ANTHROPIC_API_KEY=sk-ant-fake\n"
)

CERT_MSG = "certificate verify failed: self signed certificate in chain"
RESET_MSG = "рукопожатие оборвано: connection reset by peer"


# ── фейковый транспорт ─────────────────────────────────────────────────────
class _Resp:
    """Ответ, каким его отдаёт `urlopen`: и контекст-менеджер, и объект."""

    def __init__(self, code: int = 200, body: bytes = b"{}"):
        self.code = code
        self.status = code
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def getcode(self):
        return self.code

    def read(self, *a):
        return self._body

    def close(self):
        return None


def _http_error(code: int) -> urllib.error.HTTPError:
    """Настоящий `HTTPError`: он же одновременно и ответ, и исключение."""
    return urllib.error.HTTPError(
        "https://api.telegram.org/botX/getMe", code, "answered",
        email.message.Message(), io.BytesIO(b"{}"))


class FakeOpener:
    """Инъекция вместо сети. Поддерживает ОБА вызова — `opener(url)` и
    `opener.open(url)`: какую форму выбрала реализация, сторож не знает и знать
    не должен."""

    def __init__(self, outcome):
        self.outcome = outcome          # исключение, ответ, или dict url->…
        self.calls = []

    def _act(self, url, *a, **kw):
        # Адрес запоминается ТЕКСТОМ: реализация вправе передать сюда готовый
        # `urllib.request.Request`, и сверка по `str()` объекта сравнивала бы
        # адрес с `<Request object at 0x...>` — то есть краснела бы на форме
        # аргумента, а не на поведении.
        text = getattr(url, "full_url", None) or str(url)
        self.calls.append(text)
        outcome = self.outcome
        if isinstance(outcome, dict):
            outcome = next((v for k, v in outcome.items() if k in text),
                           _Resp(200))
        if isinstance(outcome, BaseException):
            raise outcome
        if callable(outcome):
            return outcome(url)
        return outcome

    def __call__(self, url, *a, **kw):
        return self._act(url, *a, **kw)

    def open(self, url, *a, **kw):
        return self._act(url, *a, **kw)


def _explode(*a, **kw):
    raise AssertionError(
        "сторож дошёл до НАСТОЯЩЕЙ сети — инъекция не используется")


# ── адаптеры вызова: имена параметров спекой не назначены ──────────────────
def _fn(name: str):
    fn = getattr(ow, name, None)
    if fn is None:
        pytest.fail(
            "в модуле нет `%s` (§5.1): слой замера не построен — доказательство "
            "целости провода не написано" % name)
    return fn


def _measure(url: str, opener, wire: str = "tg_api"):
    """Позвать `_reach_one`, подставившись под её сигнатуру.

    Имя параметра — плумбинг; красным обязано быть ПОВЕДЕНИЕ, а не выбор
    слова. Опознаётся только инъекция (по подстроке `open`), адрес отдаётся
    первым оставшимся обязательным параметром; форма `(имя_провода, адрес)`
    поддержана тоже.
    """
    fn = _fn("_reach_one")
    params = inspect.signature(fn).parameters
    kwargs = {n: opener for n in params if "open" in n}
    if not kwargs:
        pytest.fail(
            "`_reach_one` не принимает инъекцию транспорта: замерить её, не "
            "трогая сеть, нельзя, а сторож, ходящий в api.telegram.org, — это "
            "проба, а не сторож. Параметры: %s" % list(params))
    rest = [n for n, p in params.items()
            if n not in kwargs and p.default is inspect.Parameter.empty
            and p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)]
    if len(rest) == 1:
        kwargs[rest[0]] = url
    elif len(rest) == 2:
        kwargs[rest[0]], kwargs[rest[1]] = wire, url
    else:
        pytest.fail("не разобрал сигнатуру `_reach_one`: %s" % list(params))
    return fn(**kwargs)


def _verdict(result):
    """(ok, reason, detail) из чего бы замер ни вернул."""
    if isinstance(result, dict):
        ok, reason, detail = (result.get("ok"), result.get("reason"),
                              result.get("detail"))
    elif isinstance(result, (tuple, list)) and len(result) >= 2:
        ok, reason = result[0], result[1]
        detail = result[2] if len(result) > 2 else ""
    elif hasattr(result, "ok"):
        ok, reason, detail = (result.ok, getattr(result, "reason", None),
                              getattr(result, "detail", ""))
    else:
        pytest.fail(
            "замер вернул %r: причина отказа потеряна. Голый bool не различает "
            "подмену сертификата и отсутствие сети, а чинятся они по-разному"
            % (result,))
    assert isinstance(ok, bool), "поле `ok` не булево: %r" % (result,)
    return ok, reason, str(detail or "")


def _snapshot(env_text: str, opener):
    """Позвать `_reachability_snapshot` с текстом `.env` и инъекцией."""
    fn = _fn("_reachability_snapshot")
    params = inspect.signature(fn).parameters
    known = (("open", opener), ("env", env_text), ("prev", {}), ("state", {}),
             ("now", time.time()))
    kwargs = {}
    for name in params:
        for needle, value in known:
            if needle in name:
                kwargs[name] = value
                break
    missing = [n for n, p in params.items()
               if n not in kwargs and p.default is inspect.Parameter.empty
               and p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)]
    assert not missing, (
        "не разобрал сигнатуру `_reachability_snapshot`, обязательные "
        "параметры без значения: %s (все: %s)" % (missing, list(params)))
    if not any("env" in n for n in params):
        pytest.fail(
            "`_reachability_snapshot` не принимает текст `.env` аргументом: "
            "проверить «нет адреса → нет пробы», не подкладывая файл живой "
            "машине, нельзя. Параметры: %s" % list(params))
    return fn(**kwargs)


def _terms(snapshot) -> dict:
    assert isinstance(snapshot, dict), "снимок не словарь: %r" % (snapshot,)
    terms = snapshot.get("terms")
    assert isinstance(terms, dict), (
        "в снимке нет словаря `terms`: %r" % (snapshot,))
    return terms


# ══ А. Чем доказывается, что провод цел ════════════════════════════════════
def test_a_normal_answer_is_green():
    ok, _reason, _detail = _verdict(_measure(R2_URL, FakeOpener(_Resp(200))))
    assert ok is True


@pytest.mark.parametrize("code", [401, 403, 404, 405, 429, 500, 502, 503])
def test_any_http_answer_at_all_is_green(code):
    """Код ответа не важен — важно, что он ПРИШЁЛ.

    Пришедший код доказывает ровно то, что проба обязана доказать: TLS
    состоялся и на том конце настоящий хост. Подменённый сертификат рвёт связь
    РАНЬШЕ, чем появится любой код.

    И это же делает пробу бесплатной. Считать зелёным только 200 значило бы
    требовать УСПЕШНОГО вызова Anthropic 2880 раз в сутки — то есть платить за
    сторожа деньгами и получить повод его выключить.
    """
    ok, _reason, detail = _verdict(_measure(R2_URL, FakeOpener(_http_error(code))))
    assert ok is True, (
        "HTTP %s объявлен обрывом провода. Хост ОТВЕТИЛ — значит, провод цел; "
        "красное здесь — вечная лампа на живой связи (%s)" % (code, detail))


@pytest.mark.parametrize("raised, case", [
    (ssl.SSLCertVerificationError(CERT_MSG), "сырое исключение ssl"),
    (urllib.error.URLError(ssl.SSLCertVerificationError(CERT_MSG)),
     "как его отдаёт urlopen — завёрнутым в URLError"),
    (ssl.SSLError("WRONG_VERSION_NUMBER"), "TLS сломался иначе"),
])
def test_a_broken_certificate_is_red_with_a_tls_reason(raised, case):
    """Обе формы намеренно. Живьём `urlopen` отдаёт ошибку сертификата
    ЗАВЁРНУТОЙ в `URLError`, и реализация, разбирающая только сырую, поймала бы
    подмену ровно никогда — оставаясь зелёной в аварию.
    """
    ok, reason, detail = _verdict(_measure(R2_URL, FakeOpener(raised)))
    assert ok is False, "подмена сертификата (%s) объявлена целым проводом" % case
    assert reason == REASON_TLS, (
        "причина %r вместо %r (%s): именно она отличает подмену от «сети нет»"
        % (reason, REASON_TLS, case))
    assert detail.strip(), "красное без деталей (%s)" % case


@pytest.mark.parametrize("raised, case", [
    (urllib.error.URLError(ConnectionRefusedError(10061, "refused")), "отказ в соединении"),
    (urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed")), "DNS не разрешился"),
    (TimeoutError("timed out"), "таймаут"),
])
def test_no_answer_at_all_is_red_with_its_own_reason(raised, case):
    ok, reason, _detail = _verdict(_measure(R2_URL, FakeOpener(raised)))
    assert ok is False, "«%s» объявлено целым проводом" % case
    assert reason == REASON_NO_RESPONSE, (
        "причина %r вместо %r (%s)" % (reason, REASON_NO_RESPONSE, case))


def test_tls_and_no_response_are_two_different_words():
    """Ключевое различие, и оно про человека, а не про код.

    Одно слово на две беды — это два числа на одну вещь с другого конца:
    владелец, прочитавший «сети нет», идёт перезагружать роутер, тогда как
    сеть есть, а на том конце подменённый хост. 26.08 отказ выглядел ровно
    так: TCP-рукопожатие проходило, разрыв случался сразу после.
    """
    _ok1, tls_reason, _d1 = _verdict(_measure(
        R2_URL, FakeOpener(urllib.error.URLError(
            ssl.SSLCertVerificationError(CERT_MSG)))))
    _ok2, dead_reason, _d2 = _verdict(_measure(
        R2_URL, FakeOpener(urllib.error.URLError(
            ConnectionRefusedError(10061, "refused")))))
    assert tls_reason != dead_reason, (
        "подмена сертификата и отсутствие сети названы ОДНИМ словом %r — "
        "красное указывает не туда, и окно в 30 минут уходит на роутер"
        % (tls_reason,))


@pytest.mark.parametrize("raised, needle", [
    (urllib.error.URLError(ConnectionResetError(10054, RESET_MSG)), "reset"),
    (urllib.error.URLError(ssl.SSLCertVerificationError(CERT_MSG)), "certificate"),
])
def test_every_red_carries_the_underlying_error_text(raised, needle):
    """Причина — для машины (дедуп), текст — для человека.

    Вердикт, потерявший исходное сообщение, оставляет владельца с категорией
    вместо улики: «связь» вместо «self signed certificate in chain».
    """
    _ok, _reason, detail = _verdict(_measure(R2_URL, FakeOpener(raised)))
    assert needle in detail.lower(), (
        "в тексте вердикта нет исходной ошибки (%r): %r" % (needle, detail))


def test_the_measurement_touches_only_the_injected_transport(monkeypatch):
    """«Сеть не трогается» — замер, а не обещание.

    Сторож, дошедший до настоящего `api.telegram.org`, будит живого бота на
    машине, где его гоняют, и превращает прогон в лотерею состояния сети.
    """
    monkeypatch.setattr(urllib.request, "urlopen", _explode)
    opener = FakeOpener(_Resp(200))
    _measure(R2_URL, opener)
    assert opener.calls, "инъекция не была использована — замер сходил мимо неё"
    assert any(R2_URL in str(u) for u in opener.calls), (
        "замер ходил не по тому адресу, который ему дали: %s" % opener.calls)


# ══ Б. Провод без адреса ═══════════════════════════════════════════════════
def test_no_address_means_no_probe_at_all():
    """`R2_ENDPOINT` нет → терма `r2` НЕТ ВОВСЕ.

    Ни красного, ни зелёного. Красное было бы вечной лампой на машине, где R2
    не настроен, — то есть фоном, который перестают читать; зелёное соврало бы
    о проводе, которого никто не мерил.
    """
    snap = _snapshot(ENV_WITHOUT_R2, FakeOpener(_Resp(200)))
    terms = _terms(snap)
    assert ADDRESSED_WIRE not in terms, (
        "адреса R2 в `.env` нет, а терм `%s` в снимке есть: %r"
        % (ADDRESSED_WIRE, terms.get(ADDRESSED_WIRE)))


def test_the_absent_wire_leaves_no_trace_anywhere_in_the_snapshot():
    """Отдельно и шире: терма нет НИГДЕ, а не только в `terms`.

    Снимок этой семьи носит правду и плоско тоже. Терм, выпавший из `terms`,
    но оставшийся рядом, доехал бы до вердикта вторым путём — и «пробы нет»
    снова стало бы «проба красная».
    """
    snap = _snapshot(ENV_WITHOUT_R2, FakeOpener(_Resp(200)))
    stray = [k for k in snap if k != "terms" and ADDRESSED_WIRE == k]
    assert not stray, "терм отсутствующего провода лежит рядом с `terms`: %s" % stray


def test_an_address_in_env_brings_the_wire_back():
    """Обратный ход: правило обязано быть ПРАВИЛОМ, а не выключенным проводом.

    Без этого «нет адреса → нет пробы» неотличимо от «r2 не измеряется
    никогда», и провод бэкапов молча выпал бы из сторожа насовсем.
    """
    opener = FakeOpener(_Resp(200))
    terms = _terms(_snapshot(ENV_WITH_R2, opener))
    assert ADDRESSED_WIRE in terms, (
        "адрес R2 в `.env` задан, а терма всё равно нет: %s" % sorted(terms))
    assert any(R2_HOST in str(u) for u in opener.calls), (
        "терм `r2` есть, но по адресу из `.env` никто не ходил — вердикт "
        "выдуман, а не измерен: %s" % opener.calls)


@pytest.mark.parametrize("line, case", [
    ("%s=\n" % ADDRESS_VAR, "переменная объявлена пустой"),
    ("%s=   \n" % ADDRESS_VAR, "одни пробелы"),
])
def test_an_empty_address_counts_as_no_address(line, case):
    """Пустое значение — то же отсутствие, только тише.

    `R2_ENDPOINT=` даёт адрес «», и проба по нему краснела бы ВЕЧНО, указывая
    на несуществующую поломку сети.
    """
    snap = _snapshot(ENV_WITHOUT_R2 + line, FakeOpener(_Resp(200)))
    assert ADDRESSED_WIRE not in _terms(snap), (
        "%s, а терм `%s` в снимке есть" % (case, ADDRESSED_WIRE))


def test_a_quoted_address_is_read_like_everywhere_else():
    """Кавычки вокруг значения — обычный вид `.env`.

    В этом же модуле `parse_token` их снимает; чтение, не снимающее кавычки,
    объявило бы настроенный R2 ненастроенным и молча убрало бы провод.
    """
    env = ENV_WITHOUT_R2 + '%s="%s"\n' % (ADDRESS_VAR, R2_URL)
    assert ADDRESSED_WIRE in _terms(_snapshot(env, FakeOpener(_Resp(200)))), (
        "адрес в кавычках прочитан как отсутствующий")


@pytest.mark.parametrize("wire", ADDRESSLESS_WIRES)
@pytest.mark.parametrize("env, case", [
    (ENV_WITH_R2, "полный .env"),
    (ENV_WITHOUT_R2, "без адреса R2"),
    ("", "пустой .env"),
])
def test_the_addressless_wires_are_always_measured(wire, env, case):
    """Адрес Telegram и Anthropic — литералы, и провода эти есть ВСЕГДА.

    Ключ им не нужен: любой ответ хоста зелёный, а `401` без ключа — тоже
    ответ. Провод, тихо исчезнувший из-за пустого `.env`, дал бы ровно ту
    аварию, ради которой всё написано: 20 часов немоты при зелёном сторожe.
    """
    terms = _terms(_snapshot(env, FakeOpener(_http_error(401))))
    assert wire in terms, (
        "провод `%s` пропал из снимка (%s): %s" % (wire, case, sorted(terms)))


def test_the_snapshot_measures_every_term_it_reports(monkeypatch):
    """Столько замеров, сколько термов, — и ни один не выдуман.

    Терм, попавший в снимок без обращения к своему адресу, — зелёная лампа по
    построению; она и есть то, что 26.08 светило двадцать часов.
    """
    monkeypatch.setattr(urllib.request, "urlopen", _explode)
    opener = FakeOpener(_Resp(200))
    terms = _terms(_snapshot(ENV_WITH_R2, opener))
    assert len(set(opener.calls)) >= len(terms), (
        "термов %d, а разных адресов опрошено %d: %s"
        % (len(terms), len(set(opener.calls)), opener.calls))


def test_the_snapshot_never_reaches_the_real_network(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _explode)
    opener = FakeOpener(_Resp(200))
    _snapshot(ENV_WITH_R2, opener)
    assert opener.calls, "инъекция не использована — строитель сходил мимо неё"
