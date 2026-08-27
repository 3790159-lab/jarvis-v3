# -*- coding: utf-8 -*-
"""§5.3 спеки `2026-08-27-alarm-channel-outside-the-broken-transport.md`:
тревога обязана оставить локальный след НА ДИСКЕ **до** попытки сети и
дописать исход после.

ЗАМЕР, ИЗ КОТОРОГО ЭТО ВЫРОСЛО. `_send_tg` возвращает `bool`, и все четыре
вызова в `main()` этот результат ВЫБРАСЫВАЮТ. Доставка алерта не фиксируется
нигде: успех и провал на диске неразличимы, оба беззвучны. За 20 часов
перехвата 26–27.08 не отправилось ни одного сообщения — и не осталось ни
одной строки о том, что мы пытались.

ТРЕБОВАНИЕ, дословно: «Каждый извещатель пишет строку в локальный журнал
ПЕРЕД обращением к проводу и дописывает исход после». Из этого следуют три
разные вещи, и они проверяются порознь:

  1. строка пишется ДО сети — иначе при упавшей сети (то есть ровно в аварию)
     следа не будет вовсе: код до записи не доживёт;
  2. исход дописывается — «пытались» без «чем кончилось» не отличает немоту
     от доставки;
  3. успех и провал на диске РАЗЛИЧИМЫ — сегодняшний дефект дословно.

ГДЕ СТОИТ СТОРОЖ. На `_send_tg`: это ЕДИНСТВЕННАЯ воронка, через которую
проходят все четыре вызова из `main()`. Журналирование, размазанное по
местам вызова, было бы четырьмя копиями одного решения — и разъехались бы
они молча, как уже разъехались две сборки текстов алертов.

ЖУРНАЛ ПО КОНТРАКТУ: `state/alert_delivery.jsonl`, ДВЕ записи на тревогу —
`attempt` до провода и `result` после. Модуль обязан выставить путь к нему
КОНСТАНТОЙ: путь, зашитый внутрь функции литералом, нельзя увести на
подставное дерево, и единственным способом проверить след остался бы прогон,
пишущий в боевой `state/` — то есть сторож, портящий то, что стережёт.

Сторожа писались ОТ ТЕКСТА СПЕКИ, реализации автор не видел
([[jarvis-guards-not-by-the-plan-author]]). На момент написания следа нет —
файл ОБЯЗАН быть красным.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_SPEC = importlib.util.spec_from_file_location(
    "ops_watchdog_alert_trace", ROOT / "scripts" / "ops_watchdog.py")
ow = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = ow
_SPEC.loader.exec_module(ow)


FAKE_TOKEN = "1234567890:SECRETTOKENVALUEDONOTLOG"
ALERT_TEXT = "🚨 DOWN: BACKEND (:8010 /health). no response"
NET_ERROR = "перехват TLS: certificate verify failed"

# Имя файла — из контракта; имя КОНСТАНТЫ спекой не назначено, поэтому она
# ищется сперва по значению (`.name`), и лишь потом по имени. Красным обязано
# быть ОТСУТСТВИЕ следа, а не выбор слова. `Path`-фильтр отсекает соседей
# вроде `ALERT_GROUP_MIN`.
TRACE_FILE = "alert_delivery.jsonl"
_PATH_NAME_RE = re.compile(r"^(ALERT|SEND|DELIVER|NOTIF|TRACE|OUTBOX)[A-Z0-9_]*$")


def _trace_attr() -> str:
    by_value = [n for n, v in vars(ow).items()
                if isinstance(v, Path) and v.name == TRACE_FILE]
    if len(by_value) == 1:
        return by_value[0]
    assert not by_value, (
        "путь до %s объявлен дважды — две константы на одну правду: %s"
        % (TRACE_FILE, by_value))

    hits = [n for n, v in vars(ow).items()
            if _PATH_NAME_RE.match(n) and isinstance(v, Path)]
    if not hits:
        pytest.fail(
            "модуль не выставил константу пути к журналу тревог "
            "(§5.3, `state/%s`): доставка алерта по-прежнему не фиксируется "
            "нигде, успех и провал на диске неразличимы" % TRACE_FILE)
    if len(hits) > 1:
        narrowed = [n for n in hits
                    if any(w in n for w in ("LOG", "JOURNAL", "TRACE"))]
        assert len(narrowed) == 1, (
            "несколько кандидатов на журнал тревог — два места для одной "
            "правды: %s" % hits)
        return narrowed[0]
    return hits[0]


class _Resp:
    """Ответ Telegram, каким его видит `_send_tg`."""

    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._payload


@pytest.fixture()
def trace(tmp_path, monkeypatch):
    """Журнал тревог, уведённый на подставное дерево, + фейковый токен."""
    path = tmp_path / "state" / TRACE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ow, _trace_attr(), path)
    monkeypatch.setattr(ow, "_bot_token", lambda: FAKE_TOKEN)
    return path


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _records(path: Path):
    """Записи журнала. JSONL, если разбирается; иначе — сырые строки."""
    out = []
    for line in _text(path).splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            out.append(line)
    return out


def _digitless(text: str) -> str:
    """Текст без цифр — чтобы отличать ИСХОД, а не отметки времени."""
    return re.sub(r"\d+", "", text)


# ── 1. след пишется ДО попытки сети ────────────────────────────────────────
def test_the_trace_exists_before_the_network_is_touched(trace, monkeypatch):
    """Порядок проверяется ИЗНУТРИ попытки, а не по факту выживания.

    «Файл остался после исключения» доказывает только то, что запись была
    где-то в той же функции. Здесь сеть САМА смотрит на диск в момент, когда её
    зовут: это единственная форма, которой нельзя удовлетворить, записав след
    после вызова.
    """
    seen = {}

    def _looking_urlopen(req, *a, **kw):
        seen["exists"] = trace.exists()
        seen["bytes"] = trace.stat().st_size if trace.exists() else 0
        raise urllib.error.URLError(NET_ERROR)

    monkeypatch.setattr(urllib.request, "urlopen", _looking_urlopen)
    ow._send_tg(ALERT_TEXT)

    assert seen, "сеть не была вызвана вовсе — сторож смотрит не туда"
    assert seen["exists"] and seen["bytes"] > 0, (
        "к моменту обращения к проводу следа на диске нет: при упавшей сети "
        "(то есть ровно в аварию) до записи дело не дойдёт")


def test_the_trace_survives_a_network_that_raises(trace, monkeypatch):
    def _boom(req, *a, **kw):
        raise urllib.error.URLError(NET_ERROR)

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    assert ow._send_tg(ALERT_TEXT) is False

    assert _records(trace), (
        "сеть упала — и на диске снова пусто: немота не оставила улик")


def test_the_trace_carries_the_alert_it_tried_to_deliver(trace, monkeypatch):
    """«Пытались отправить» без «что именно» не восстанавливает историю.

    Вернувшийся к машине человек обязан увидеть, о ЧЁМ ему не смогли сказать.
    """
    def _boom(req, *a, **kw):
        raise urllib.error.URLError(NET_ERROR)

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    ow._send_tg(ALERT_TEXT)

    body = _text(trace)
    assert "BACKEND" in body, "в следе нет текста тревоги: %r" % body


# ── 2. исход дописывается ──────────────────────────────────────────────────
def test_the_failure_reason_is_written_down(trace, monkeypatch):
    """Исход провала обязан назвать причину.

    Единственной уликой сегодня остаётся `rc` Планировщика, причём журнал
    `TaskScheduler/Operational` ВЫКЛЮЧЕН, и следующий прогон затирает её
    навсегда. Причина в своём журнале — то, что переживает следующий прогон.
    """
    def _boom(req, *a, **kw):
        raise urllib.error.URLError(NET_ERROR)

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    ow._send_tg(ALERT_TEXT)

    body = _text(trace)
    assert "certificate verify failed" in body or "TLS" in body, (
        "исход записан без причины — «не смогли» без «почему»: %r" % body)


def test_success_and_failure_are_distinguishable_on_disk(tmp_path, monkeypatch):
    """ДЕФЕКТ ДОСЛОВНО: сегодня успех и провал на диске неразличимы.

    Сверка идёт по тексту БЕЗ ЦИФР: отметки времени различаются всегда, и
    сравнение сырых файлов зеленело бы даже у журнала, пишущего одно и то же.
    """
    attr = _trace_attr()
    monkeypatch.setattr(ow, "_bot_token", lambda: FAKE_TOKEN)

    fail_path = tmp_path / "fail" / TRACE_FILE
    fail_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ow, attr, fail_path)

    def _boom(req, *a, **kw):
        raise urllib.error.URLError(NET_ERROR)

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    assert ow._send_tg(ALERT_TEXT) is False

    ok_path = tmp_path / "ok" / TRACE_FILE
    ok_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ow, attr, ok_path)
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, *a, **kw: _Resp(b'{"ok": true}'))
    assert ow._send_tg(ALERT_TEXT) is True

    assert _records(fail_path) and _records(ok_path), (
        "одна из двух попыток не оставила следа вовсе")
    assert _digitless(_text(fail_path)) != _digitless(_text(ok_path)), (
        "провал и успех записаны ОДИНАКОВО — журнал есть, а различить по нему "
        "немоту от доставки по-прежнему нельзя:\nпровал: %r\nуспех: %r"
        % (_text(fail_path), _text(ok_path)))


def test_the_ok_trace_does_not_claim_a_failure(trace, monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, *a, **kw: _Resp(b'{"ok": true}'))
    ow._send_tg(ALERT_TEXT)
    assert "certificate verify failed" not in _text(trace)


def test_telegram_saying_not_ok_is_recorded_as_a_failure(trace, monkeypatch):
    """HTTP 200 с `{"ok": false}` — доставка НЕ состоялась.

    Провод отвечает, а сообщение не ушло: заблокированный бот, сменённый
    chat_id, отозванный токен. Журнал, записывающий «отправлено» по факту
    ответа, был бы третьим способом соврать успехом.
    """
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda req, *a, **kw: _Resp(b'{"ok": false, "description": "bot blocked"}'))
    assert ow._send_tg(ALERT_TEXT) is False

    body = _digitless(_text(trace))
    assert body.strip(), "ответ «ok: false» не оставил следа вовсе"
    assert "false" in body.lower() or "fail" in body.lower() \
        or "blocked" in body.lower(), (
        "недоставка при живом проводе записана как обычная попытка: %r" % body)


def test_one_alert_leaves_two_records_attempt_and_result(trace, monkeypatch):
    """Контракт: `attempt` ДО провода и `result` ПОСЛЕ — две записи, не одна.

    Одна запись «отправлено/не отправлено», сделанная после возврата из сети,
    не отличает «попытались и не смогли» от «не дошли даже до попытки»: при
    падении процесса посреди отправки её не будет вовсе. Пара записей — это и
    есть то, что переживает падение между ними.
    """
    def _boom(req, *a, **kw):
        raise urllib.error.URLError(NET_ERROR)

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    ow._send_tg(ALERT_TEXT)

    records = _records(trace)
    assert len(records) == 2, (
        "на одну тревогу записей %d, а контракт §5.3 — две (`attempt` и "
        "`result`): %s" % (len(records), records))
    body = _text(trace).lower()
    assert "attempt" in body, "нет записи о ПОПЫТКЕ: %r" % _text(trace)
    assert "result" in body, "нет записи об ИСХОДЕ: %r" % _text(trace)


def test_the_attempt_record_is_complete_before_the_result_exists(trace, monkeypatch):
    """В момент обращения к проводу на диске уже лежит `attempt` — и только он.

    Проверяется ИЗНУТРИ попытки: журнал, дописанный обеими записями разом
    после возврата из сети, здесь и ловится — при упавшей сети до дописывания
    дело не доходит, и на диске не остаётся ничего.
    """
    seen = {}

    def _looking(req, *a, **kw):
        seen["text"] = _text(trace)
        raise urllib.error.URLError(NET_ERROR)

    monkeypatch.setattr(urllib.request, "urlopen", _looking)
    ow._send_tg(ALERT_TEXT)

    assert seen, "сеть не была вызвана вовсе — сторож смотрит не туда"
    mid = seen["text"].lower()
    assert "attempt" in mid, (
        "к моменту обращения к проводу записи о попытке нет: %r" % seen["text"])
    assert "result" not in mid, (
        "исход записан ДО того, как он стал известен: %r" % seen["text"])


# ── 3. журнал накапливается и не течёт секретом ────────────────────────────
def test_attempts_accumulate_so_the_count_can_be_shown(trace, monkeypatch):
    """«Сколько раз мы пытались и не смогли» — величина из спеки.

    Журнал, который перезаписывается каждой попыткой, отвечает на этот вопрос
    «один раз» при любом числе попыток.
    """
    def _boom(req, *a, **kw):
        raise urllib.error.URLError(NET_ERROR)

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    ow._send_tg("🚨 первая тревога")
    ow._send_tg("🚨 вторая тревога")

    body = _text(trace)
    assert "первая" in body and "вторая" in body, (
        "вторая попытка затёрла первую — счёт немоты не восстановить: %r" % body)
    assert len(_records(trace)) == 4, (
        "две тревоги по две записи — это четыре строки, а их %d: %s"
        % (len(_records(trace)), _records(trace)))


def test_the_trace_never_writes_the_bot_token(trace, monkeypatch):
    """Журнал ложится в `state/`, а `state/` уезжает в бэкап на R2.

    След, включивший URL целиком, был бы ровно тем дефектом, который закрывали
    DEV-74/75: учётные данные в данных ([[jarvis-dev74-token-at-rest]]).
    """
    def _boom(req, *a, **kw):
        raise urllib.error.URLError(NET_ERROR)

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    ow._send_tg(ALERT_TEXT)

    body = _text(trace)
    assert FAKE_TOKEN not in body, "в журнале тревог лежит токен целиком"
    assert "SECRETTOKENVALUE" not in body, "в журнале тревог лежит секретная часть"


def test_a_missing_token_is_also_recorded(trace, monkeypatch):
    """Нет токена — извещатель нем ещё до провода, и это тоже обязано остаться.

    Сегодня этот путь — `return False` в первой строке: самая тихая из всех
    форм немоты, и единственная, которую нельзя отличить от «тревог не было».
    """
    monkeypatch.setattr(ow, "_bot_token", lambda: "")

    def _never(req, *a, **kw):
        raise AssertionError("без токена в сеть ходить нечем")

    monkeypatch.setattr(urllib.request, "urlopen", _never)
    assert ow._send_tg(ALERT_TEXT) is False

    assert _records(trace), (
        "извещатель промолчал из-за отсутствия токена и не оставил следа")
