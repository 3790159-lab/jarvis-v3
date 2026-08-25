# -*- coding: utf-8 -*-
"""Лампа арки «панель учится отправлять»: ручки инстанса и 18-я проба.

Спека `docs/superpowers/specs/2026-08-25-panel-sends.md` (§3 п.3, §10 п.7) и
`CONTRACT_panel_sends.md` §7–§8. Решение владельца 25.08: «лампа берётся
СРАЗУ», то есть ручка на инстансе и проба `ops_watchdog` едут вместе с
очередью, а не «потом».

СТОРОЖА ПИСАНЫ ОТ СПЕКИ И КОНТРАКТА. Реализацию писал другой автор в другом
дереве; его кода автор этих сторожей не видел и не искал
([[jarvis-guards-not-by-the-plan-author]]).

ЗАЧЕМ ЛАМПА ВООБЩЕ. Владелец нажал «отправить», а раннер лежит. Сегодня это
тишина: панель сказала «поставлено», задание живёт в очереди, и узнает об этом
человек, только если сам откроет панель. Величина «сколько задание ждёт»
сегодня не измеряется НИГДЕ — ровно как возраст незакрытой карточки эскалации
до 24.08.

ЧТО ЗДЕСЬ ОХРАНЯЕТСЯ, и все четыре ломаются ТИХО:

1. ЦЕНА ОТВЕТА. `/ops/outgoing` не аутентифицирована — её тело читает любой,
   кто дотянулся до порта в тайнете. Форма пиннится ЛИТЕРАЛЬНЫМ списком в ОБЕ
   стороны: лишний ключ «для удобства диагностики» (слаг, contact_id, ТЕКСТ
   СООБЩЕНИЯ) уедет постороннему, пропавший — сделает пробу вечно красной,
   то есть фоном.
2. «НЕ СМОГЛИ СПРОСИТЬ» — КРАСНОЕ. Проба, которая при любой поломке говорит
   «очередь чиста», хуже отсутствующей: на зелёное никто не смотрит, в том и
   смысл зелёного ([[jarvis-loud-failure-next-to-a-soothing-lamp]]).
3. ВОЗРАСТ НЕ В `reason`. `reason` — ключ дедупа, а возраст растёт каждый
   цикл: секунды в нём дали бы алерт раз в 30 секунд.
4. НЕЧИТАЕМАЯ БД — 500, А НЕ ПУСТОЙ ОТВЕТ. Пустой ответ на сломанном чтении
   зелен ПО ПОСТРОЕНИЮ.

ЧЕГО ЗДЕСЬ НЕТ: ни сети, ни живого `.secrets/`, ни живого порта. Инстанс
поднимается `TestClient`ом на базе в `tmp_path`, снимки для пробы собираются
словарями.

ИМЕНА КОНТРАКТА берутся через `getattr(..., умолчание)` НАМЕРЕННО: пока
реализации нет, обращение к отсутствующей константе на уровне модуля сорвало
бы СБОР всего файла, и вместо честно красных сторожей была бы одна ошибка
коллекции — то есть ноль измеренных утверждений.
"""
from __future__ import annotations

import ast
import importlib
import json
import importlib.util as _ilu
import inspect
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chatter.storage.db import Store

REPO_ROOT = Path(__file__).resolve().parent.parent
OPS_PATH = REPO_ROOT / "scripts" / "ops_watchdog.py"
CLIENTS_DIR = REPO_ROOT / "chatter" / "clients"

_spec = _ilu.spec_from_file_location("ops_watchdog_outgoing", OPS_PATH)
ow = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(ow)

# Путь ручки — ЛИТЕРАЛ контракта §7, а не выведенная из модуля константа:
# литерал и есть предмет договора. Равенство литерала тому, что знает проба,
# проверяется отдельным сторожем — это два места на одну строку, и разъехаться
# они обязаны громко.
OUTGOING_PATH = "/ops/outgoing"
ENQUEUE_PATH = "/api/outgoing"

# Контракт §7: РОВНО эти четыре ключа и ничего больше.
EXPECTED_BODY_KEYS = {"pending", "oldest_age_s", "stuck", "refused"}

# Контракт §8: ШЕСТЬ исходов, и каждый чинится по-разному.
EXPECTED_REASONS = ("no_instance", "no_bind_address", "no_response",
                    "http:500", "bad_payload", "outgoing_stuck")

KEY = "yarina-panel-key"
SLUG = "yarina"
A = "111:yarina"
B = "222:yarina"
HOST = "100.102.179.47"
PORT = ow.PANEL_CLIENT_PORT
DAY = 86400.0

# Возраст, который НЕ имеет права попасть в `reason`: величина взята заведомо
# приметная, чтобы её появление в ключе дедупа нельзя было списать на
# совпадение цифр.
LOUD_AGE = 987654.0


# ── стенд инстанса ──────────────────────────────────────────────────────────

@pytest.fixture()
def stand(tmp_path, monkeypatch):
    """Фабрика инстанса клиента: своя БД в `tmp_path`, свой слаг, свой ключ.

    Возвращает `(app, db_path)`. Живой `.secrets/` не трогается никогда.
    """
    built = {"n": 0}

    def build(jobs=(), refused=(), sent=(), *, now=None):
        now = time.time() if now is None else now
        # 🔴 НОВЫЙ ФАЙЛ НА КАЖДЫЙ ВЫЗОВ, а не `unlink` старого. Ручка открывает
        # свой `Store` внутри запроса, и закрыть его отсюда нечем: на Windows
        # удаление файла, который держит открытым чужое соединение, — это
        # `PermissionError [WinError 32]`, то есть падение сторожа по вине
        # сторожа. Сторож обязан краснеть от кода, а не от собственной уборки.
        built["n"] += 1
        db = tmp_path / ("yarina-%d.db" % built["n"])
        s = Store(str(db))
        for i, (cid, age, text) in enumerate(jobs):
            s.get_or_create_contact(cid)
            s.enqueue_outgoing(cid, text, token="pending-%d" % i, now=now - age)
        for i, (cid, text) in enumerate(refused):
            s.get_or_create_contact(cid)
            rid, _ = s.enqueue_outgoing(cid, text, token="refused-%d" % i, now=now)
            s.mark_outgoing_failed(rid, error="окно канала закрыто", now=now,
                                   terminal=True)
        for i, (cid, text) in enumerate(sent):
            s.get_or_create_contact(cid)
            rid, _ = s.enqueue_outgoing(cid, text, token="sent-%d" % i, now=now)
            s.mark_outgoing_sent(rid, msg_id="700%d" % i, now=now)
        s.close()

        beat = tmp_path / "state" / "chatter_heartbeat_yarina.txt"
        beat.parent.mkdir(parents=True, exist_ok=True)
        beat.write_text("beat", encoding="utf-8")

        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("JARVIS_PANELS_KEY", KEY)
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


def _body(api) -> dict:
    r = TestClient(api).get(OUTGOING_PATH)
    assert r.status_code == 200, (
        "ручка `%s` ответила %s — проверять нечего: %r"
        % (OUTGOING_PATH, r.status_code, r.text[:300]))
    return r.json()


# ═══ §7 контракта: РУЧКА `/ops/outgoing` ════════════════════════════════════

def test_ruchka_otvechaet_BEZ_klyucha(stand):
    """У watchdog'а ключа клиента нет и быть не должно (§7 контракта,
    дословно как у `/ops/attention`). Ручка за ключом — это лампа, которую
    некому зажечь."""
    api, _db = stand(jobs=[(A, 60.0, "жду отправки")])
    r = TestClient(api).get(OUTGOING_PATH)
    assert r.status_code == 200, (r.status_code, r.text[:300])


def test_ruchka_ne_redirect_na_formu_vhoda(stand):
    """Попав под `install_panel_auth_redirect`, ручка отдала бы пробе 200 от
    СТРАНИЦЫ ЛОГИНА — то есть лампа позеленела бы на мёртвой панели. Ровно
    этот дефект уже чинили в `/ops/attention` 22.08."""
    api, _db = stand(jobs=[(A, 60.0, "жду отправки")])
    r = TestClient(api).get(OUTGOING_PATH, follow_redirects=False)
    assert r.status_code == 200, (r.status_code, r.headers.get("location"))
    assert "location" not in {k.lower() for k in r.headers}, dict(r.headers)
    assert "<form" not in r.text.lower(), r.text[:300]


def test_telo_neset_ROVNO_chetyre_klyucha_kontrakta(stand):
    """🔴 ЛИТЕРАЛЬНЫЙ пин формы в ОБЕ стороны (§7 контракта).

    «Нет лишних» — граница безопасности: ручка не аутентифицирована, и ключ,
    добавленный «чтобы было видно, что застряло» (contact_id, слаг, ТЕКСТ
    СООБЩЕНИЯ ВЛАДЕЛЬЦА), уедет любому, кто дотянулся до порта, и заметить
    это будет некому.

    «Нет пропавших» — граница наблюдаемости: проба объявит `bad_payload`, и
    лампа станет вечно красной, то есть фоном.

    Список ЛИТЕРАЛЬНЫЙ, а не выведенный из ручки: выведенный согласен с
    ручкой по определению и промолчит ровно там, где ручка забыла ключ.
    """
    api, _db = stand(jobs=[(A, 60.0, "жду отправки")])
    body = _body(api)
    assert isinstance(body, dict), body
    got = set(body)
    assert got == EXPECTED_BODY_KEYS, (
        "форма ответа разошлась с контрактом §7.\n  лишние: %s\n  пропали: %s"
        % (sorted(got - EXPECTED_BODY_KEYS), sorted(EXPECTED_BODY_KEYS - got)))


@pytest.mark.parametrize("secret", [
    SLUG, "yarina.db", ".secrets", A, "жду отправки", "окно канала закрыто",
])
def test_ruchka_ne_vydayot_nichego_o_kliente(stand, secret):
    """Разбито параметрами намеренно: одно утёкшее имя обязано называть СЕБЯ,
    а не тонуть в общем «в теле что-то не то».

    Тело берётся с требованием 200: без него сторож ЗЕЛЕН ПО ПОСТРОЕНИЮ, пока
    ручки нет — в теле 404-го ответа секретов нет по той простой причине, что
    там нет ничего.
    """
    api, _db = stand(jobs=[(A, 60.0, "жду отправки")],
                     refused=[(B, "не уехало")])
    r = TestClient(api).get(OUTGOING_PATH)
    assert r.status_code == 200, (
        "ручка не отвечает 200 — проверять на утечку нечего: %s %r"
        % (r.status_code, r.text[:200]))
    assert secret.lower() not in r.text.lower(), (
        "`%s` отдал наружу «%s»: %r" % (OUTGOING_PATH, secret, r.text[:400]))


def test_chisla_prostyh_json_tipov(stand):
    """Строка вместо числа («2 задания») прочтётся пробой как `bad_payload`, и
    новость «задание застряло» подменится новостью «ручка сломалась» —
    чинятся они в разных местах и разными людьми."""
    api, _db = stand(jobs=[(A, 60.0, "жду отправки")])
    body = _body(api)
    assert isinstance(body["pending"], int) and not isinstance(body["pending"], bool), body
    assert isinstance(body["refused"], int) and not isinstance(body["refused"], bool), body
    assert isinstance(body["stuck"], bool), (
        "`stuck` — %r: контракт §7 требует булев, а не число и не строку"
        % (body["stuck"],))
    assert body["oldest_age_s"] is None or isinstance(body["oldest_age_s"], (int, float)), body


def test_pustaya_ochered_eto_nuli_i_None_a_ne_otsutstvie_otveta(stand):
    """Пусто — ЗАКОННОЕ состояние, и `None` в возрасте, а НЕ ноль: ноль секунд
    читается как «задание положили только что», то есть как самая свежая беда
    вместо её отсутствия."""
    api, _db = stand()
    body = _body(api)
    assert body["pending"] == 0 and body["refused"] == 0, body
    assert body["oldest_age_s"] is None, (
        "пустая очередь отдала возраст %r вместо None: «заданий нет» и "
        "«задание только что положили» стали неотличимы" % (body["oldest_age_s"],))
    assert body["stuck"] is False, body


def test_schitayutsya_zadaniya_OBOIH_kontaktov(stand):
    """ДВА контакта, потому что реализация может считать очередь «текущего
    диалога»: тогда лампа зелена ровно до тех пор, пока смотрят не туда."""
    api, _db = stand(jobs=[(A, 60.0, "первому"), (B, 120.0, "второму")])
    assert _body(api)["pending"] == 2, _body(api)


def test_otpravlennoe_ne_schitaetsya_ni_pending_ni_refused(stand):
    """Успешно ушедшее задание — не новость. Считая его, лампа краснела бы тем
    сильнее, чем лучше работает отправка, и очень быстро стала бы фоном."""
    api, _db = stand(jobs=[(A, 60.0, "ещё жду")],
                     sent=[(B, "уже уехало"), (A, "и это уехало")])
    body = _body(api)
    assert body["pending"] == 1, body
    assert body["refused"] == 0, body


def test_otkazannye_schitayutsya_OTDELNYM_chislom(stand):
    """Отказ и ожидание — РАЗНЫЕ новости и чинятся по-разному: «застряло»
    лечится подъёмом раннера, «отказано» — разбором причины, которую видит
    только владелец. Слив их в одно число, лампа перестанет различать
    «раннер лежит» и «канал закрыт»."""
    api, _db = stand(jobs=[(A, 60.0, "жду")],
                     refused=[(A, "первый отказ"), (B, "второй отказ")])
    body = _body(api)
    assert body["pending"] == 1, body
    assert body["refused"] == 2, body


def test_vozrast_prinadlezhit_SAMOMU_STAROMU_zadaniyu(stand):
    """Величина, которая тем меньше, чем хуже дела, — это сторож, врущий ровно
    в аварии. Именно так уже занижался возраст в `/ops/attention`: сортировка
    убывающая, срез `limit`, и самая старая карточка выпадала молча."""
    api, _db = stand(jobs=[(A, 3 * DAY, "старое"), (B, 60.0, "свежее")])
    body = _body(api)
    assert body["pending"] == 2, body
    assert body["oldest_age_s"] == pytest.approx(3 * DAY, abs=300), (
        "возраст взят у МОЛОДОГО задания (%r): чем дольше лежит очередь, тем "
        "меньшую беду покажет лампа" % (body["oldest_age_s"],))


def test_svezhee_zadanie_ne_zastryalo_a_drevnee_zastryalo(stand):
    """§7 контракта: «застряло или нет» решает ИНСТАНС своим порогом.

    Числа порога сторож не знает НАМЕРЕННО: второе число на ту же вещь
    погасило бы первое молча ([[jarvis-two-numbers-for-one-thing]]). Поэтому
    проверяется НАПРАВЛЕНИЕ, а не значение: свежая минута — не беда, тридцать
    суток — беда при любом мыслимом пороге.

    Обе половины в одном стороже: `stuck = False` всегда проходит первую
    половину, `stuck = True` всегда — вторую. Порознь любая из констант
    прошла бы как «реализация».
    """
    api, _db = stand(jobs=[(A, 60.0, "только что нажали")])
    fresh = _body(api)
    assert fresh["pending"] == 1, fresh
    assert fresh["stuck"] is False, (
        "минутное задание объявлено застрявшим: лампа будет красной на каждом "
        "нажатии кнопки и станет фоном за день; %r" % (fresh,))

    api, _db = stand(jobs=[(A, 30 * DAY, "лежит месяц")])
    ancient = _body(api)
    assert ancient["pending"] == 1, ancient
    assert ancient["stuck"] is True, (
        "задание возрастом в ТРИДЦАТЬ СУТОК не считается застрявшим: значит "
        "`stuck` не считается вовсе, и вся лампа зелена по построению; %r"
        % (ancient,))


def test_stuck_ne_vzvoditsya_na_pustoi_ocheredi(stand):
    """Нечему застрять. `stuck=True` без заданий — это красное, которое нечем
    чинить, а красное, которое нечем чинить, приучает не смотреть."""
    api, _db = stand(refused=[(A, "давно отказано")])
    body = _body(api)
    assert body["pending"] == 0, body
    assert body["stuck"] is False, (
        "очередь пуста, а `stuck` взведён: чинить нечего, а лампа красная; %r"
        % (body,))


def test_nechitaemaya_baza_eto_500_a_NE_pustoi_otvet(stand):
    """🔴 DEV-18 и §7 контракта дословно: «Нечитаемая БД — HTTP 500, не пустой
    ответ».

    Пустой ответ «всё тихо» на сломанном чтении — зелёное ПО ПОСТРОЕНИЮ,
    худший из возможных отказов: лампа горит зелёным именно потому, что мерить
    перестали. Проба обязана получить `http:500` и сказать «не знаю», а не
    «очередь чиста».
    """
    api, db = stand(jobs=[(A, 60.0, "жду")])
    Path(db).write_bytes(b"not-a-sqlite-file-just-garbage" * 100)
    r = TestClient(api, raise_server_exceptions=False).get(OUTGOING_PATH)
    assert r.status_code == 500, (
        "нечитаемая БД дала HTTP %s вместо 500: %r — лампа скажет «очередь "
        "чиста» ровно потому, что читать перестали"
        % (r.status_code, r.text[:300]))


# ── §7 контракта: POST `/api/outgoing` ──────────────────────────────────────

def _post(api, *, key: str | None, contact_id: str, text: str, token: str):
    client = TestClient(api)
    if key is not None:
        client.cookies.set("panels_key", key)
    return client.post(ENQUEUE_PATH, data={
        "contact_id": contact_id, "text": text, "event_token": token})


def test_zapis_zadaniya_trebuet_vladelca(stand):
    """§7 контракта: ручка ЗА `require_owner`, как остальные ручки панели.

    Открытая ручка записи — это возможность написать ЛИДУ КЛИЕНТА от его же
    имени любому, кто дотянулся до порта. Ошибка тут не «утечка чисел», а
    чужие слова в чужом диалоге, и отличить их потом будет нечем: по
    транспорту они наши, по автору — человеческие."""
    api, db = stand()
    r = _post(api, key=None, contact_id=A, text="привет от чужого", token="tok-x")
    assert r.status_code != 200, (
        "задание записано БЕЗ ключа владельца (HTTP %s): любой, кто дотянулся "
        "до порта, пишет лидам клиента от его имени" % (r.status_code,))
    s = Store(db)
    try:
        assert s.pending_outgoing() == [], (
            "неавторизованный запрос всё-таки положил задание в очередь: %r"
            % (s.pending_outgoing(),))
    finally:
        s.close()


def test_otvet_paneli_nesyot_id_i_priznak_povtora(stand):
    """§7 контракта: ответ РОВНО `{"queued", "id", "duplicate"}`.

    `duplicate` — не украшение: это единственный способ панели показать
    владельцу «ты уже это отправил», не отправив второй раз. Без него двойное
    нажатие выглядит как два успешных действия."""
    api, _db = stand()
    r = _post(api, key=KEY, contact_id=A, text="первое нажатие", token="tok-1")
    assert r.status_code == 200, (r.status_code, r.text[:300])
    body = r.json()
    assert set(body) == {"queued", "id", "duplicate"}, (
        "форма ответа записи разошлась с контрактом §7: %r" % (body,))
    assert body["queued"] is True, body
    assert isinstance(body["id"], int) and not isinstance(body["id"], bool), body
    assert body["duplicate"] is False, body


def test_dvoinoe_nazhatie_daet_duplicate_i_ODNU_stroku(stand):
    """Двойное нажатие кнопки — не гипотеза, а свойство кнопки.

    Три утверждения в одном, и порознь они не заменяют друг друга: повтор
    ОБЪЯВЛЕН повтором (иначе панель покажет владельцу два успеха), вернул ТОТ
    ЖЕ id (иначе панель покажет две строки), и очередь содержит ОДНУ строку
    (иначе лид получит два сообщения)."""
    api, db = stand()
    first = _post(api, key=KEY, contact_id=A, text="одно и то же", token="tok-same")
    second = _post(api, key=KEY, contact_id=A, text="одно и то же", token="tok-same")
    assert first.status_code == 200 and second.status_code == 200, (
        first.status_code, second.status_code, second.text[:300])
    assert second.json()["duplicate"] is True, (
        "повтор по тому же токену объявлен НОВЫМ заданием: %r" % (second.json(),))
    assert second.json()["id"] == first.json()["id"], (
        "повтор вернул другой id: %r против %r"
        % (second.json()["id"], first.json()["id"]))

    s = Store(db)
    try:
        rows = s.pending_outgoing()
        assert len(rows) == 1, (
            "два нажатия дали %d заданий в очереди: лид получит дубль; %r"
            % (len(rows), rows))
    finally:
        s.close()


def test_pustoi_tekst_iz_paneli_ne_lozhitsya_v_ochered(stand):
    """Пустое задание — это либо пустота лиду, либо вечно неотправимая строка,
    которая будет держать лампу красной до конца времён. Отказ обязан быть
    ЗДЕСЬ, а не в раннере: строка, уже легшая в базу, требует человека."""
    api, db = stand()
    r = _post(api, key=KEY, contact_id=A, text="   ", token="tok-empty")
    assert r.status_code != 200 or r.json().get("queued") is not True, (
        "панель приняла пустое задание как поставленное: %s %r"
        % (r.status_code, r.text[:300]))
    s = Store(db)
    try:
        assert s.pending_outgoing() == [], (
            "пустое задание легло в очередь: %r" % (s.pending_outgoing(),))
    finally:
        s.close()


# ═══ §8 контракта: ВОСЕМНАДЦАТАЯ ПРОБА ══════════════════════════════════════

def _snap(host=HOST, port=PORT, status=200, payload=None, problem=None,
          slug=SLUG, instance=True, **extra):
    """Снимок для `probe_outgoing`, по образцу `probe_attention`.

    Контракт фиксирует форму снимка ЧАСТИЧНО: «форма и исходы КАК У
    `probe_attention`». Поля адреса и статуса взяты оттуда дословно; под каким
    именем лежит РАЗОБРАННОЕ ТЕЛО, контракт не называет, поэтому тело кладётся
    сразу под несколько правдоподобных имён. Сторож пинит ИСХОД пробы, а не
    внутреннее имя ключа снимка: пинить неназванное значило бы краснеть на
    законном выборе автора кода.
    """
    snap = {"host": host, "port": port, "status": status, "problem": problem,
            "slug": slug, "instance": instance}
    for name in ("payload", "body", "json", "data", "outgoing"):
        snap[name] = payload
    snap.update(extra)
    return snap


def _payload(pending=2, oldest=LOUD_AGE, stuck=True, refused=0):
    """Тело ручки контракта §7 — РОВНО четыре ключа."""
    return {"pending": pending, "oldest_age_s": oldest, "stuck": stuck,
            "refused": refused}


def _probe(snapshot):
    fn = getattr(ow, "probe_outgoing", None)
    assert fn is not None, (
        "функции `probe_outgoing` нет вовсе — 18-й пробы не существует, а "
        "решение владельца 25.08 требовало брать лампу СРАЗУ")
    return fn(snapshot)


def test_proba_prinimaet_ODIN_pozicionnyi_snimok():
    """Россыпь именованных аргументов уже стоила вердикта `no_bind_address` на
    СОВЕРШЕННО ЗДОРОВОЙ панели: снимок уезжал в первый позиционный, `host`
    оставался пустым. Форма подписи здесь — не стиль, а тот самый дефект."""
    fn = getattr(ow, "probe_outgoing", None)
    assert fn is not None, "функции `probe_outgoing` нет вовсе"
    params = list(inspect.signature(fn).parameters.values())
    positional = [p for p in params
                  if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    assert len(positional) == 1, (
        "у `probe_outgoing` %d позиционных параметров вместо одного снимка: %s"
        % (len(positional), [p.name for p in params]))
    assert positional[0].name == "snapshot", positional[0].name


def test_probe_i_ruchka_nazyvayut_ODIN_put():
    """Два места на одну строку: путь ручки в инстансе и путь, по которому
    ходит проба. Разъехавшись, проба получит 404 («ручки ещё нет») на живой
    ручке, и лампа станет вечно красной.

    Ищется ЛИТЕРАЛ в константах модуля через AST, а не по имени константы:
    имени контракт не назвал, а путь — назвал. Комментарий с тем же текстом
    AST'ом не виден, и это ровно та слепота, которая здесь нужна
    ([[jarvis-approved-text-three-places]])."""
    tree = ast.parse(OPS_PATH.read_text(encoding="utf-8"))
    literals = {n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert OUTGOING_PATH in literals, (
        "в `scripts/ops_watchdog.py` нет строки %r: проба не знает, куда "
        "ходить, либо ходит по другому пути" % OUTGOING_PATH)


# ── исходы (§8 контракта: те же шесть, что у `probe_attention`) ─────────────

def test_tihaya_ochered_chitaetsya_kak_ok():
    """Ответила, `stuck == False` — задания уезжают. Ожидающие задания при
    этом БЫТЬ МОГУТ: «есть неотправленное» и «висит неотправленным сутки» —
    разные новости, и первая не авария."""
    p = _probe(_snap(payload=_payload(pending=3, oldest=12.0, stuck=False)))
    assert p["ok"] is True, p


def test_zastryavshaya_ochered_eto_novost_PRO_DELO():
    p = _probe(_snap(payload=_payload(pending=2, stuck=True)))
    assert p["ok"] is False, p
    assert p["reason"] == "outgoing_stuck", (
        "вердикт о застрявшей очереди назван %r вместо 'outgoing_stuck': "
        "ключ дедупа не тот, и склейка алертов пройдёт мимо" % (p.get("reason"),))


def test_klient_BEZ_INSTANSA_eto_KRASNOE():
    """🔴 «Не смогли спросить» — КРАСНОЕ, а не зелёное и не отсутствие пробы.

    Состав проб берётся из РОСТЕРА, а не из списка инстансов (§8 контракта).
    Инстанс сегодня один, а клиентов больше — проба, построенная «по
    инстансам», была бы ЗЕЛЁНОЙ ПО ПОСТРОЕНИЮ ровно там, где лежит вся
    проблема: у клиента без панели задание из панели всё равно может лежать в
    очереди, и спросить об этом некого.
    """
    p = _probe(_snap(instance=False, host=None, status=None, payload=None))
    assert p["ok"] is False, (
        "клиент без инстанса объявлен здоровым: лампа зелена именно там, где "
        "мерить нечем; %r" % (p,))
    assert p["reason"] == "no_instance", p


def test_ne_vychislivshiisya_adres_eto_otdelnaya_novost():
    """«Мерить нечем» и «очередь застряла» чинятся разными людьми: первое —
    правкой резолвера адреса, второе — подъёмом раннера. Один вердикт на два
    случая отправляет чинить не туда."""
    p = _probe(_snap(host=None, status=None, payload=None,
                     problem="адрес 0.0.0.0 отвергнут"))
    assert p["ok"] is False, p
    assert p["reason"] == "no_bind_address", p


def test_molchashchii_port_eto_no_response():
    p = _probe(_snap(status=None, payload=None))
    assert p["ok"] is False, p
    assert p["reason"] == "no_response", p


def test_404_chitaetsya_kak_RUCHKI_ESHCHO_NET_a_ne_kak_smert():
    """🔴 ПОРЯДОК ДЕПЛОЯ. Сначала ручка (рестарт инстанса), ПОТОМ проба.

    Обратный порядок даёт красное на здоровом — а красное на здоровом учит не
    смотреть. Значит 404 обязан читаться как «инстанс не перезапущен», и
    отличаться от «инстанс мёртв» СЛОВОМ, а не только оттенком."""
    missing = _probe(_snap(status=404, payload=None))
    dead = _probe(_snap(status=None, payload=None))
    assert missing["ok"] is False and missing["reason"] == "http:404", missing
    assert missing["reason"] != dead["reason"], (
        "«ручки ещё нет» и «инстанс мёртв» получили ОДИН вердикт: разбор "
        "начнётся с перезапуска живого процесса; %r / %r" % (missing, dead))


def test_zhivoi_process_otvechayushchii_ploho_eto_svoya_prichina():
    """500 — это нечитаемая БД инстанса (§7 контракта). Слив её с «нет
    ответа», проба отправит чинить процесс вместо базы."""
    p = _probe(_snap(status=500, payload=None))
    assert p["ok"] is False and p["reason"] == "http:500", p


@pytest.mark.parametrize("body, why", [
    (None, "тела нет вовсе"),
    ("очередь пуста", "тело — строка, а не объект"),
    ([], "тело — список"),
    ({}, "тело пустое"),
    ({"pending": 2}, "нет `stuck` — не по чему выносить вердикт"),
    ({"stuck": True}, "нет `pending`"),
    ({"pending": "два", "oldest_age_s": 1.0, "stuck": False, "refused": 0},
     "`pending` строкой"),
    ({"pending": 2, "oldest_age_s": 1.0, "stuck": "да", "refused": 0},
     "`stuck` строкой вместо булева"),
    ({"pending": -1, "oldest_age_s": 1.0, "stuck": False, "refused": 0},
     "отрицательное `pending`"),
])
def test_telo_ne_toi_formy_eto_KRASNOE(body, why):
    """🔴 `bad_payload` ОБЯЗАН БЫТЬ КРАСНЫМ.

    Соблазн «не разобрали тело — значит всё тихо» даёт пробу, которая при
    ЛЮБОЙ поломке ручки говорит «задания уезжают». Такая хуже отсутствующей:
    на зелёное никто не смотрит, в том и смысл зелёного.

    Разбито параметрами: одна пропущенная форма тела обязана называть себя.
    """
    p = _probe(_snap(payload=body))
    assert p["ok"] is False, (
        "тело не той формы (%s) прочтено как ЗДОРОВАЯ очередь: %r" % (why, p))
    assert p["reason"] == "bad_payload", (
        "тело не той формы (%s) получило вердикт %r вместо `bad_payload` — "
        "поломка ручки подменилась новостью о деле" % (why, p.get("reason")))


def test_ISHODOV_ROVNO_SHEST_i_VSE_SHEST_KRASNYE():
    """Литеральный пин набора исходов (§8 контракта).

    Исходов ровно шесть, все шесть красные, и ПЯТЬ из них — новости ПРО
    НАБЛЮДЕНИЕ («не знаю, уезжают ли задания»), а не про дело. «Не знаю»
    здесь громче, чем «всё хорошо», потому что зелёное по построению — это
    отказ, которого никто не увидит.

    Пин ЛИТЕРАЛЬНЫМ списком, а не перебором того, что вернула реализация:
    перебор согласен с реализацией по определению и промолчит ровно про
    забытый исход ([[jarvis-literal-lists-not-introspection]]).
    """
    cases = {
        "no_instance": _snap(instance=False, host=None, status=None, payload=None),
        "no_bind_address": _snap(host=None, status=None, payload=None,
                                 problem="адреса нет"),
        "no_response": _snap(status=None, payload=None),
        "http:500": _snap(status=500, payload=None),
        "bad_payload": _snap(payload={"pending": 1}),
        "outgoing_stuck": _snap(payload=_payload(stuck=True)),
    }
    assert set(cases) == set(EXPECTED_REASONS), sorted(cases)
    for expected, snapshot in cases.items():
        p = _probe(snapshot)
        assert p["ok"] is False, (
            "исход %r объявлен зелёным: %r" % (expected, p))
        assert p["reason"] == expected, (
            "исход %r назван %r — ключ дедупа не тот, склейка и подавление "
            "алертов пройдут мимо" % (expected, p.get("reason")))
    green = _probe(_snap(payload=_payload(pending=1, oldest=5.0, stuck=False)))
    assert green["ok"] is True, (
        "зелёного исхода нет вовсе: проба, красная всегда, — не сторож, а "
        "фон ([[jarvis-gate-mutates-the-deploy-tree]]); %r" % (green,))


# ── §8 контракта: ВОЗРАСТ В `reason` НЕ ПОПАДАЕТ НИКОГДА ───────────────────

@pytest.mark.parametrize("name, snapshot", [
    ("no_instance", _snap(instance=False, host=None, status=None, payload=None)),
    ("no_bind_address", _snap(host=None, status=None, payload=None, problem="нет")),
    ("no_response", _snap(status=None, payload=None)),
    ("bad_payload", _snap(payload={"pending": 1, "oldest_age_s": LOUD_AGE})),
    ("outgoing_stuck", _snap(payload=_payload(oldest=LOUD_AGE, stuck=True))),
])
def test_vozrast_ne_popadaet_v_reason_NI_V_ODNOM_ishode(name, snapshot):
    """🔴 `reason` — КЛЮЧ ДЕДУПА, а возраст растёт КАЖДЫЙ цикл.

    Секунды в ключе означают алерт раз в 30 секунд: пятнадцать записей об
    одном событии за минуту — шум, который однажды спрячет настоящее (DEV-66).

    Проверяются ВСЕ исходы, а не только «застряло»: возраст удобно вписать
    именно в тот вердикт, который кажется безобидным, и `http:404` в этом
    списке стоит нарочно — цифры кода допустимы, цифры ВОЗРАСТА нет.
    """
    p = _probe(snapshot)
    reason = str(p.get("reason", ""))
    assert str(int(LOUD_AGE)) not in reason, (
        "в причине исхода %s живёт возраст: %r — каждый цикл даст новый ключ "
        "дедупа, то есть новый алерт" % (name, reason))
    assert HOST not in reason, (
        "в причине исхода %s живёт адрес: %r — при смене адреса тайнета "
        "дедуп сбросится на здоровой системе" % (name, reason))


def test_detail_HRANIT_chisla_i_adres():
    """Парная граница: числа и адрес не выброшены, а ПЕРЕНЕСЕНЫ в `detail`.

    Без них разбор снова начинается с догадки — куда ходила проба и насколько
    всё плохо. Сторож на один только `reason` разрешил бы «вычистить цифры»
    отовсюду сразу."""
    p = _probe(_snap(payload=_payload(pending=4, oldest=LOUD_AGE, stuck=True)))
    detail = str(p.get("detail", ""))
    assert HOST in detail, ("адрес не назван в detail: %r" % detail)
    assert any(ch.isdigit() for ch in detail), (
        "в detail не осталось ни одного числа: владелец не узнает, СКОЛЬКО "
        "заданий лежит и сколько они лежат; %r" % detail)


# Слова, любым из которых текст 18-й пробы имеет право назвать свою беду.
# ЛИТЕРАЛЬНЫЙ список и он РАСШИРЕН 25.08: первая редакция знала только
# «отправ / задани / очеред» и не приняла формулировку «не уехало из панели»,
# которая называет беду ничуть не хуже. Расширение названо вслух, потому что
# молча подогнанный под реализацию список — это сторож, согласный с кодом по
# определению ([[jarvis-literal-lists-not-introspection]]).
#
# Предмет договора здесь — НЕ вокабуляр, а то, что текст говорит про
# НЕОТПРАВКУ. Поэтому рядом стоит второе утверждение, от слов не зависящее
# вовсе: текст 18-й пробы обязан отличаться от текста 11-й.
DEED_WORDS = ("уехал", "отправ", "задани", "очеред", "не ушл", "не дошл")


def test_detail_govorit_o_NEOTPRAVKE_a_ne_o_kartochkah():
    """Владелец читает ФРАЗУ, а не имя переменной.

    18-я проба и 11-я живут рядом, ходят на соседние ручки ОДНОГО инстанса и
    по форме — близнецы. Текст, скопированный вместе с формой, отправил бы
    владельца открывать карточки эскалации вместо подъёма раннера — то есть
    чинить не ту беду, а настоящая осталась бы висеть.

    Два утверждения разной природы, и второе важнее первого: первое зависит от
    моего списка слов, второе — нет. Совпадение текстов двух семейств было бы
    доказательством копипасты независимо от того, какие слова я угадал.
    """
    p = _probe(_snap(payload=_payload(pending=2, stuck=True)))
    detail = str(p.get("detail", "")).lower()
    assert "карточ" not in detail, (
        "текст 18-й пробы говорит о карточках эскалации: %r" % detail)
    assert any(word in detail for word in DEED_WORDS), (
        "в тексте пробы нет ни одного слова про неотправку (искали %s): "
        "владелец не поймёт, что именно сломалось: %r" % (list(DEED_WORDS), detail))

    twin = ow.probe_attention({
        "slug": SLUG, "instance": True, "host": HOST, "port": PORT,
        "status": 200, "problem": None,
        "payload": {"open": 2, "stale_open": 2, "oldest_age_s": LOUD_AGE,
                    "oldest_wait_s": LOUD_AGE}})
    assert detail.strip() != str(twin.get("detail", "")).strip().lower(), (
        "тексты 18-й и 11-й проб СОВПАЛИ: два разных семейства говорят "
        "владельцу одно и то же, и одно из них он починит не в том месте")


# ═══ §8 контракта: ПРОВОДКА ПРОБЫ В ЦИКЛ ════════════════════════════════════
#
# 🔴 ЗАЧЕМ ЭТОТ РАЗДЕЛ. Проба может быть написана верно и не звучать в цикле
# вовсе — тогда лампы нет, а выглядит это как «лампа зелёная». На этом уже
# обожглись 25.08: ключ лежал зелёный, а пробы за ним не существовало. Поэтому
# здесь пиннится не сама проба, а ПУТЬ от ростера до ключа алерта.
#
# Имена приехали дополнением к контракту, гадать не надо: `_outgoing_snapshot`,
# `_client_ops_snapshot`, `SNAPSHOT_FAMILIES`, именованный `outgoing_snapshot`
# у `probe_all` ПОСЛЕ `attention_snapshot`, префикс семейства `outgoing:`.

OUT_PREFIX = "outgoing:"
ATT_PREFIX = "escalation:"

WITH_INSTANCE = ow.PANEL_CLIENT_SLUG
NO_INSTANCE = "volska" if ow.PANEL_CLIENT_SLUG != "volska" else "yarina"
DISABLED = "demo"

ROSTER_TWO = {"clients": [{"slug": NO_INSTANCE, "enabled": True},
                          {"slug": WITH_INSTANCE, "enabled": True},
                          {"slug": DISABLED, "enabled": False}]}


class _FakeResp:
    """Ответ `urlopen`: контекстный менеджер, код и тело. Формы доступа — все
    расхожие сразу, потому что каким именно способом сборщик читает ответ,
    контракт не называет."""

    def __init__(self, code, body):
        self.code = code
        self.status = code
        self._body = body if isinstance(body, bytes) else body.encode("utf-8")
        self.headers = {"Content-Type": "application/json"}

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def getcode(self):
        return self.code

    def read(self, *_a):
        return self._body

    def info(self):
        return self.headers


def _chatter(roster=None):
    return {"processes": [], "beats": {}, "legacy_beat_age": 90 * DAY,
            "guardian_beat_age": 5.0, "guardian_lock_pid": None,
            "root": "C:/jarvis",
            "roster": ROSTER_TWO if roster is None else roster}


def _panel(host=HOST, port=PORT, status=200, problem=None):
    """Снимок панели ровно той формы, какую отдаёт `_panel_client_snapshot`."""
    return {"host": host, "port": port, "status": status, "problem": problem}


@pytest.fixture()
def net(monkeypatch):
    """Сеть инстанса под контролем — перехват на `urllib`, а не на `fetch`.

    Инъекцию `fetch` контракт называет, а ФОРМУ её ответа — нет, и угадывать
    неназванное значит краснеть на законном выборе автора кода. Поэтому
    сборщик гоняется через СВОЙ настоящий сетевой слой, перехваченный там, где
    watchdog обязан оставаться stdlib-only. Заодно это доказывает поход
    целиком: адрес, порт, ПУТЬ и разбор тела.
    """
    state = {"status": 200, "silent": False, "urls": [],
             "body": _payload(pending=1, oldest=42.0, stuck=False, refused=0)}

    def fake_urlopen(url, *_a, **_k):
        target = getattr(url, "full_url", url)
        state["urls"].append(str(target))
        if state["silent"]:
            raise OSError("порт молчит (refused)")
        if state["status"] != 200:
            raise ow.urllib.error.HTTPError(
                str(target), state["status"], "nope", {}, None)
        body = state["body"]
        text = body if isinstance(body, str) else json.dumps(body)
        return _FakeResp(200, text)

    monkeypatch.setattr(ow.urllib.request, "urlopen", fake_urlopen)
    return state


def _collect(roster=None, panel=None):
    """Снимок исходящих НАСТОЯЩИМ сборщиком контракта."""
    collector = getattr(ow, "_outgoing_snapshot", None)
    assert collector is not None, (
        "`_outgoing_snapshot` не заведён — снимок для 18-й пробы не собирается "
        "ничем, и проба в цикле не появится вовсе")
    return collector(ROSTER_TWO if roster is None else roster,
                     _panel() if panel is None else panel)


def _probes(outgoing_snapshot, roster=None):
    return ow.probe_all(lambda _p: 200,
                        lambda _p: (100 * 2 ** 30, 0, 50 * 2 ** 30),
                        chatter_snapshot=_chatter(roster),
                        attention_snapshot=None,
                        outgoing_snapshot=outgoing_snapshot)


def _out_keys(probes):
    return sorted(k for k in probes if k.startswith(OUT_PREFIX))


def _ok(slug: str) -> str:
    return OUT_PREFIX + slug


def test_probe_all_poluchil_outgoing_snapshot_POSLE_attention_snapshot():
    """Порядок параметров назван контрактом, и это не педантизм.

    `probe_all` зовут позиционно в нескольких местах дерева; параметр,
    вставленный ПЕРЕД `attention_snapshot`, сдвинул бы снимок эскалаций в
    чужой аргумент — и семейство, которое сегодня работает, потухло бы молча.
    """
    params = list(inspect.signature(ow.probe_all).parameters.values())
    names = [p.name for p in params]
    assert "outgoing_snapshot" in names, (
        "у `probe_all` нет параметра `outgoing_snapshot`: снимку 18-й пробы "
        "некуда доехать, и в цикле её не будет; параметры %s" % (names,))
    assert "attention_snapshot" in names, names
    assert names.index("outgoing_snapshot") > names.index("attention_snapshot"), (
        "`outgoing_snapshot` стоит РАНЬШЕ `attention_snapshot` (%s): "
        "позиционные вызывающие стороны сдвинут снимок эскалаций в чужой "
        "аргумент, и работающее сегодня семейство потухнет молча" % (names,))
    default = {p.name: p for p in params}["outgoing_snapshot"].default
    assert default is None, (
        "умолчание `outgoing_snapshot` — %r вместо None: watchdog не имеет "
        "права слать вердикт о том, чего не мерил" % (default,))


def test_sborshchiki_snimka_sushchestvuyut_pod_imenami_kontrakta():
    """Без сборщика проба — функция, которую никто не зовёт."""
    assert callable(getattr(ow, "_outgoing_snapshot", None)), (
        "`_outgoing_snapshot` не заведён")
    assert callable(getattr(ow, "_client_ops_snapshot", None)), (
        "`_client_ops_snapshot` не заведён — общий сборщик пер-клиентных "
        "снимков, названный контрактом")


def test_semeistvo_obyavleno_v_SNAPSHOT_FAMILIES_i_ne_vytesnilo_eskalacii():
    """Реестр семейств — то место, где решается, чьи ключи вообще бывают.

    Проверяются ОБА имени: новое семейство обязано появиться, а старое —
    остаться. Правка, которая заменяет одно другим, выглядит как успех ровно
    до первого невзятого алерта об эскалациях.

    Форма реестра контрактом не названа, поэтому строки собираются из объекта
    рекурсивно: пинить неназванную структуру значило бы краснеть на законном
    выборе автора кода.
    """
    families = getattr(ow, "SNAPSHOT_FAMILIES", None)
    assert families is not None, "`SNAPSHOT_FAMILIES` не заведён"

    seen: set = set()

    def walk(obj, depth=0):
        if depth > 4:
            return
        if isinstance(obj, str):
            seen.add(obj)
        elif isinstance(obj, dict):
            for k, v in obj.items():
                walk(k, depth + 1)
                walk(v, depth + 1)
        elif isinstance(obj, (list, tuple, set, frozenset)):
            for v in obj:
                walk(v, depth + 1)

    walk(families)
    assert OUT_PREFIX in seen, (
        "префикса %r нет в `SNAPSHOT_FAMILIES` (%r): ключи 18-й пробы не "
        "принадлежат ни одному семейству, а значит проедут мимо дедупа, "
        "склейки и прунинга" % (OUT_PREFIX, sorted(seen)))
    assert ATT_PREFIX in seen, (
        "префикс эскалаций %r пропал из `SNAPSHOT_FAMILIES` (%r): новое "
        "семейство вытеснило работающее" % (ATT_PREFIX, sorted(seen)))


def test_cikl_probit_KAZHDOGO_VKLYUCHENNOGO_klienta_ROSTERA(net):
    """🔴 СОСТАВ БЕРЁТСЯ ИЗ РОСТЕРА, А НЕ ИЗ СПИСКА ИНСТАНСОВ.

    Инстанс сегодня ОДИН, а клиентов больше. Задание из панели может лежать
    неотправленным у любого из них — проба, построенная «по инстансам», была
    бы ЗЕЛЁНОЙ ПО ПОСТРОЕНИЮ ровно у тех, о ком спросить некого. Это буквально
    механизм, который молчит, потому что смотрит не туда.

    Ростер из двух включённых = ДВА ключа. Выключенный клиент пробы не имеет
    ВОВСЕ — ни красной, ни зелёной: у выключенного очереди нет по определению,
    и красное на нём приучало бы не смотреть.
    """
    probes = _probes(_collect())
    assert _out_keys(probes) == sorted([_ok(NO_INSTANCE), _ok(WITH_INSTANCE)]), (
        "состав проб исходящих взят не из ростера: %s" % (_out_keys(probes),))
    assert _ok(DISABLED) not in probes, (
        "выключенный клиент получил пробу исходящих: у выключенного очереди "
        "нет по определению, и красное на нём — чистый фон")


def test_klient_bez_instansa_v_CIKLE_krasnyi_no_instance(net):
    """Тот же вердикт, что и в юните, но добытый ЧЕРЕЗ ВЕСЬ путь.

    Юнит доказывает, что функция умеет сказать `no_instance`; этот сторож — что
    сказанное доезжает до ключа алерта. Порознь они не заменяют друг друга:
    верная функция, не подключённая к циклу, — это ровно та беда, ради которой
    раздел написан.
    """
    p = _probes(_collect())[_ok(NO_INSTANCE)]
    assert p["ok"] is False, (
        "клиент без инстанса позеленел в цикле: «не смог спросить» выдано за "
        "«задания уезжают»; %r" % (p,))
    assert p["reason"] == "no_instance", p
    assert (p.get("detail") or "").strip(), (
        "вердикт без текста: владельцу нечего читать; %r" % (p,))


def test_klient_s_instansom_meryaetsya_normalno(net):
    """Предпосылка всех сторожей выше: здоровый клиент даёт ЗЕЛЁНОЕ.

    Без неё «состав из ростера» доказывался бы на пробе, красной всегда, а
    проба, красная всегда, — не сторож, а фон.
    """
    p = _probes(_collect())[_ok(WITH_INSTANCE)]
    assert p["ok"] is True, (
        "здоровый инстанс с непустой, но не застрявшей очередью объявлен "
        "больным: %r" % (p,))


def test_proba_hodit_imenno_na_ops_outgoing(net):
    """Путь доказывается ПОХОДОМ, а не совпадением константы.

    Разъехавшись с ручкой, проба получит 404 на живом инстансе — и лампа
    станет вечно красной, то есть фоном.
    """
    _collect()
    assert net["urls"], "сборщик не сходил никуда: мерить нечем"
    assert any(OUTGOING_PATH in u for u in net["urls"]), (
        "сборщик не ходил на %r: %r" % (OUTGOING_PATH, net["urls"]))
    assert any(HOST in u for u in net["urls"]), (
        "сборщик ходил не по адресу из снимка панели: %r" % (net["urls"],))


def test_BEZ_SNIMKA_prob_semeistva_NET_a_NE_ZELYONYE(net):
    """🔴 ГЛАВНЫЙ СТОРОЖ РАЗДЕЛА, и обе половины обязаны стоять рядом.

    `None` вместо снимка означает «спросить было нечем» — watchdog не на
    деплой-хосте, ростер не прочитан. Вердикт DOWN о том, чего не мерили, —
    враньё; вердикт OK о том, чего не мерили, — хуже: это зелёная лампа над
    механизмом, которого не существует. Ровно этот дефект уже стоил дня
    25.08: ключ лежал зелёный, а пробы за ним не было.

    Половина «со снимком ключи ЕСТЬ» стоит здесь же, потому что без неё
    сторож зелен по построению: «ключей нет» верно и для реализации, которая
    не завела семейство вовсе.
    """
    without = _probes(None)
    assert _out_keys(without) == [], (
        "без снимка в цикле всё-таки появились пробы исходящих: %s — они "
        "выносят вердикт о том, чего никто не мерил" % (_out_keys(without),))

    with_snapshot = _probes(_collect())
    assert _out_keys(with_snapshot), (
        "со снимком ключей семейства тоже нет: снимок до `probe_all` не "
        "доезжает, и 18-й пробы в цикле не существует")


def test_nechitaemyi_roster_ne_daet_prob_semeistva(net):
    """Ростер не прочитан — состав спросить нечем, и об этом уже краснеет
    отдельная проба ростера. Второе красное на ту же беду — шум, а шум
    однажды спрячет настоящее."""
    snapshot = _collect(roster={"error": "реестр не читается"})
    assert snapshot is None, (
        "сборщик собрал снимок на нечитаемом ростере: %r" % (snapshot,))
    assert _out_keys(_probes(snapshot)) == [], (
        "на нечитаемом ростере в цикле появились пробы исходящих")


def test_bez_snimka_paneli_merit_nechem(net):
    """Снимка панели нет = watchdog не на деплой-хосте. Проб семейства нет
    вовсе — как и у эскалаций, и по той же причине."""
    assert _collect(panel=None) is None, (
        "сборщик собрал снимок без снимка панели: адрес взялся откуда-то "
        "ещё, то есть завёлся ВТОРОЙ источник правды об адресе")


def test_klyuchi_novogo_semeistva_ne_stalkivayutsya_s_eskalacionnymi(net):
    """Два пер-клиентных семейства на одном слаге. Столкнувшись ключами, они
    затрут вердикты друг друга: владелец увидит одну беду вместо двух, и
    какая именно уцелеет — вопрос порядка сборки словаря."""
    probes = _probes(_collect())
    out = set(_out_keys(probes))
    att = {k for k in probes if k.startswith(ATT_PREFIX)}
    assert out and not (out & att), (
        "ключи семейств пересеклись: %s" % sorted(out & att))
    for key in out:
        assert key.startswith(OUT_PREFIX), key
        assert key[len(OUT_PREFIX):], (
            "ключ %r не несёт слага: пер-клиентная проба без имени клиента "
            "не говорит, У КОГО лежит задание" % (key,))
