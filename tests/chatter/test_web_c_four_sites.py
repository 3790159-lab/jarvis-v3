# -*- coding: utf-8 -*-
"""Сторожа 15 и 16 спеки «ВЕБ, волна 2 / пара C» — ЧЕТЫРЕ МЕСТА §1.2 ПОИМЁННО.

Спека: `docs/superpowers/specs/2026-08-28-web-c-envelope-identity.md`,
§1.2, §9 (сторожа 15 и 16).

Здесь мерится ИСХОД, а не текст: сторож 13 доказывает, что ручного разбора в
этих четырёх местах не осталось, а этот файл — что после правки они делают
СВОЮ РАБОТУ на трёхсегментном `contact_id`. Одно без другого зелено врёт:
место можно «починить», выкинув разбор вместе с работой.

ЖИВОГО НИЧЕГО НЕ ТРОГАЕМ: база — в `tmp_path`, Telethon-клиент — рукописный
двойник (НЕ `MagicMock`: автомок истинен и отвечает «да» на любой вопрос,
[[jarvis-magicmock-truthy-spins-the-loop]]), сети нет.
"""
from __future__ import annotations

import asyncio
import dataclasses
import random

import pytest

import chatter.telethon_run as tr
from chatter.config.loader import load_config
from chatter.core.brain import Brain
from chatter.core.llm import FakeLLM
from chatter.payments.money import Money
from chatter.run import Deps
from chatter.storage.db import Store
from chatter.telethon_run import PersonaBundle, TelethonRunner
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CLIENTS_DIR = REPO_ROOT / "chatter" / "clients"

# Трёхсегментная форма §3.1 — та, ради которой арка и пишется.
THREE = "telegram:111:demo"
PEER = 111

# Канон дрил-контактов ПОСЛЕ К1 (§4.3: «три копии DRILL_CONTACTS» едут вместе
# с кодом). Литералом, а не преобразованием живого канона: преобразование
# согласится с любым каноном, включая пустой.
DRILL_THREE = frozenset({
    "telegram:237616472:volska",
    "telegram:8849893367:volska",
    "telegram:8849893367:yarina",
})


# ═══ оснастка `deliver_outgoing` ════════════════════════════════════════════

class _Msg:
    def __init__(self, msg_id: int):
        self.id = msg_id


class _TypingCtx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False


class _Sends:
    """Единственная воронка отправки. Считает, что увидел бы ЛИД (адресат не
    `"me"`): карточки владельцу уходят тем же вызовом, и смешать их с репликой
    лиду значит перестать различать ровно то, что здесь и меряется."""

    def __init__(self):
        self.calls: list[tuple] = []

    async def __call__(self, chat, text=None, *a, **kw):
        self.calls.append((chat, text))
        return _Msg(9001)

    def to_lead(self) -> list[tuple]:
        return [c for c in self.calls if c[0] != "me"]


class _ClientDouble:
    """Явный двойник Telethon-клиента: ровно те три имени, которые спрашивает
    `deliver_outgoing`. Автомок здесь погасил бы все ветки отказа разом."""

    def __init__(self, sends: _Sends):
        self.send_message = sends
        self.entity_calls: list = []

    async def get_input_entity(self, peer_id):
        self.entity_calls.append(peer_id)
        return "entity:%s" % peer_id

    def action(self, *_a, **_k):
        return _TypingCtx()


def _runner(store: Store):
    cfg = load_config(CLIENTS_DIR, "demo")
    assert cfg.settings.telegram is not None, (
        "предпосылка стенда: у персоны demo канал ПОДКЛЮЧЁН, иначе «доставка "
        "прошла» проверять не на чем")
    deps = Deps(cfg=cfg, store=store, brain=Brain(FakeLLM(scripted=[]), cfg),
                rng=random.Random(0), clock=lambda: 1000.0, sleep=lambda _s: None)
    sends = _Sends()
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
    runner = TelethonRunner(
        client=_ClientDouble(sends), personas={"demo": PersonaBundle(cfg=cfg, deps=deps)},
        primary_slug="demo", allowlist=frozenset({PEER}), loop=loop, funnel_gate=False)
    return runner, sends


# ═══ Сторож 15, место 1: `telethon_run.deliver_outgoing` ════════════════════

def test_guard15_zadanie_iz_paneli_DOEZZHAET_do_lida_na_novoy_forme(tmp_path):
    """Сторож 15 (§1.2, место 1): задание из `outgoing_queue` контакту НОВОГО
    формата доставляется, а не превращается в `Refusal("unknown_contact")`.

    🔴 Это самое дорогое из четырёх и единственное ГРОМКОЕ. Разбор там строгий
    и fail-closed по построению: `rpartition(":")` даёт голову `"telegram:111"`,
    `int()` на ней бросает, отказ становится `Refusal`. Отказ ВИДЕН владельцу
    (`last_error`, лампа возраста неотправленного) — и ровно поэтому опасен
    иначе: любое задание контакту нового формата не доставится НИКОГДА, а
    продукт при этом стоит и выглядит работающим.

    Меряем ИСХОД (лид получил текст), а не «функция не бросила»: `Refusal`
    наружу не летит никогда — он состояние строки очереди."""
    store = Store(str(tmp_path / "queue.db"))
    try:
        store.get_or_create_contact(THREE)
        row_id, _created = store.enqueue_outgoing(
            THREE, "здравствуйте, это владелец", token="tok-web-c", now=1000.0)
        runner, sends = _runner(store)
        row = next(r for r in store.pending_outgoing() if int(r["id"]) == row_id)
        verdict = asyncio.run(tr.deliver_outgoing(runner, row, now=1000.0))
        assert verdict == "sent", (
            "доставка задания контакту %r дала вердикт %r вместо 'sent'. "
            "Задание из панели — единственный путь «человек написал лиду», и "
            "на новом формате он обязан работать, иначе владелец пишет в "
            "пустоту с красной лампой." % (THREE, verdict))
        assert len(sends.to_lead()) == 1, (
            "лид получил %d сообщений вместо одного: %r"
            % (len(sends.to_lead()), sends.to_lead()))
        assert runner.client.entity_calls == [PEER], (
            "адресат резолвился как %r, ждали %r. Голова %r — это КАНАЛ, и "
            "принять её за собеседника значит отправить не тому человеку."
            % (runner.client.entity_calls, [PEER], THREE.split(":")[0]))
    finally:
        store.close()


# ═══ Сторож 15, места 2 и 3: дрил-контакты клиента ══════════════════════════

def test_guard15_drill_ids_actions_nahodyat_kontakty_na_novoy_forme(monkeypatch):
    """Сторож 15 (§1.2, место 2): `connect/actions._drill_ids` находит
    дрил-id клиента на трёхсегментном каноне.

    🔇 Молчаливый исход: сегодня `partition(":")` даёт `persona == "111"` и
    сравнение со слугом не сходится — список выходит ПУСТЫМ, а пустой список
    дрил-контактов выглядит как «у клиента их нет», то есть как штатная
    настройка, а не как поломка.

    Канон подменяется ЛИТЕРАЛЬНЫМ трёхсегментным (§4.3 везёт три его копии
    вместе с К1): сторож обязан судить функцию, а не тот канон, который
    случайно лежит в дереве в день прогона."""
    from chatter.connect import actions

    monkeypatch.setattr(actions, "DRILL_CONTACTS", DRILL_THREE, raising=True)
    got = actions._drill_ids("volska")
    assert got == [237616472, 8849893367], (
        "_drill_ids('volska') на каноне %r вернул %r, ждали "
        "[237616472, 8849893367]. Пусто здесь — это прогон, который «не нашёл "
        "дрил-контакта», то есть отказ без следа."
        % (sorted(DRILL_THREE), got))
    assert actions._drill_ids("yarina") == [8849893367], (
        "суффикс — ПЕРСОНА: один и тот же дрил-аккаунт под двумя персонами "
        "даёт разные contact_id, и чужой суффикс увёл бы прогон к другому "
        "клиенту.")


def test_guard15_drill_ids_probes_nahodyat_kontakty_na_novoy_forme(monkeypatch):
    """Сторож 15 (§1.2, место 3): `connect/probes._drill_contact_ids` — то же.

    🔇 Проба S9 скажет «дрил-контакта нет», хотя он есть: зелёная лампа на
    месте, где ничего не проверено ([[jarvis-loud-failure-next-to-a-soothing-lamp]])."""
    from chatter.connect import probes

    monkeypatch.setattr(probes, "DRILL_CONTACTS", DRILL_THREE, raising=True)
    got = probes._drill_contact_ids("volska")
    assert sorted(got) == [237616472, 8849893367], (
        "_drill_contact_ids('volska') на трёхсегментном каноне вернул %r, "
        "ждали [237616472, 8849893367]." % (got,))


def test_guard15_kanon_DRILL_CONTACTS_pereehal_na_tryohsegmentnuyu_formu():
    """Сторож 15, предпосылка мест 2-3: сам канон переехал (К1, §4.3).

    Без этого два сторожа выше проверяют функции на каноне, которого в дереве
    нет: функции научились новой форме, канон остался в старой, и живой прогон
    так и не находит дрил-контакта. Это ровно «два числа на одну вещь»
    ([[jarvis-two-numbers-for-one-thing]]) — только числа тут форма."""
    from chatter.payments.drill_gate import DRILL_CONTACTS

    bad = sorted(c for c in DRILL_CONTACTS if c.count(":") != 2)
    assert not bad, (
        "в `payments/drill_gate.DRILL_CONTACTS` осталась старая форма: %r. "
        "К1 везёт ТРИ копии канона (§4.3); частичный переезд копий держат "
        "существующие сторожа (`tests/test_drill_contacts_sync.py`, C7 "
        "онбординга), но форму — только этот." % (bad,))


# ═══ Сторож 15, место 4: `Store._slug` в PRIMARY KEY счёта ══════════════════

def test_guard15_quote_id_sobiraetsya_iz_SLUGA_a_ne_iz_hvosta(tmp_path):
    """Сторож 15 (§1.2, место 4): `Store._slug` даёт `volska`, и `quote_id`
    получается `Q-volska-000001`.

    🔇 Молча и НАВСЕГДА: `partition(":")` берёт хвост ПОСЛЕ ПЕРВОГО двоеточия,
    то есть на новой форме — `8849893367:volska`, и id котировки становится
    `Q-8849893367:volska-000001`. Id счёта не переписывают: дефект остаётся в
    PRIMARY KEY навсегда, и увидят его в отчёте, а не в логе."""
    cid = "telegram:8849893367:volska"
    with Store(tmp_path / "money.db") as s:
        s.get_or_create_contact(cid)
        q = s.create_quote(contact_id=cid, position_id="logo_create", step_idx=0,
                           amount=Money(40000, "USD"), scope_key="logo_full",
                           amount_source="price_upper", knowledge_version="v1",
                           origin_msg_id="11", now=1.0)
        assert q["quote_id"] == "Q-volska-000001", (
            "quote_id вышел %r, ждали 'Q-volska-000001'. Если внутри видно "
            "'8849893367:volska' — хвост взят по ПЕРВОМУ двоеточию, и он "
            "уехал в PRIMARY KEY." % (q["quote_id"],))
        inv = s.create_invoice(contact_id=cid, origin_msg_id="11",
                               amount=Money(40000, "USD"), channel_id=None,
                               due_ts=None, created_by="bot", amount_source="quote",
                               status="issued", now=2.0, quote_id=q["quote_id"])
        assert inv["invoice_id"] == "INV-volska-000001", (
            "invoice_id вышел %r, ждали 'INV-volska-000001' — тот же дефект "
            "во второй таблице." % (inv["invoice_id"],))


# ═══ Сторож 16: ВСТРЕЧНАЯ ПОЛОВИНА — целое не «причесали» ═══════════════════

def test_guard16_esc_active_klyuch_chitaetsya_TSELIKOM_v_lente_paneli(tmp_path):
    """Сторож 16 (§1.2 «что НЕ ломается»): `tamapi_metrics` берёт ВЕСЬ хвост
    ключа `esc_active:<contact_id>` и на новой форме работает как работал.

    Правка, «причесавшая» и это место под общий шаблон (например заменив
    `split(":", 1)[1]` на разбор через `contact_ref`), отрезала бы у ключа
    голову канала — и панель начала бы искать контакт `111:demo` там, где
    лежит `telegram:111:demo`. Лента показала бы ПУСТО: карточка есть, лида в
    ленте нет, и это молчаливо.

    Меряем ИСХОД на настоящей базе `mode=ro`, а не текст: ровно так панель и
    ходит (§4.2 — `Store` она не открывает)."""
    from chatter.core.escalation import esc_active_key
    from app.services.tamapi_metrics import dialog_feed, needs_attention

    db = tmp_path / "panel.db"
    with Store(db) as s:
        s.get_or_create_contact(THREE)
        s.add_message(THREE, "user", "мне нужен логотип", ts=1000.0)
        s.add_card(msg_id=555, contact_id=THREE, kind="escalation", ts=1000.0)
        s.set_runtime_flag(esc_active_key(THREE), "bot:111:555", ts=1000.0)

    rows = needs_attention(str(db), now=1100.0)
    ids = [r.get("contact_id") for r in rows]
    assert THREE in ids, (
        "`needs_attention` не нашла контакт %r по флагу `esc_active:%s` "
        "(вернула %r). Значит хвост ключа берётся НЕ целиком — карточка "
        "владельцу есть, а в ленте панели лида нет." % (THREE, THREE, ids))

    feed_ids = [r.get("contact_id") for r in dialog_feed(str(db), now=1100.0)]
    assert THREE in feed_ids, (
        "`dialog_feed` не показала контакт %r (вернула %r)." % (THREE, feed_ids))


def test_guard16_slug_hvost_zhivyot_persona_settings_na_novoy_forme(tmp_path):
    """Сторож 16, вторая половина: `_persona_settings` на трёхсегментной форме
    находит НУЖНУЮ персону, а не сваливается на primary.

    §1.2: `rsplit(":", 1)[-1]` — это СЛУГ-хвост, и удлинение головы он
    переживает. Проверяем это ИСХОДОМ: слуг должен находиться, и `personas.get`
    не должен молча отдать primary. Молчаливый провал здесь — карточка
    владельцу на языке ЧУЖОЙ персоны, без единой строки в логе."""
    store = Store(str(tmp_path / "persona.db"))
    try:
        # `demo` / `demo2`, а НЕ живые клиентки: их `requisites.yaml` в git не
        # лежит, и в свежем worktree конфиг не грузится вовсе
        # ([[jarvis-worktree-missing-gitignored-client-config]]). Красное от
        # СРЕДЫ неотличимо здесь от красного по делу, а значит бесполезно.
        cfg_demo = load_config(CLIENTS_DIR, "demo")
        cfg_second = load_config(CLIENTS_DIR, "demo2")
        deps = Deps(cfg=cfg_demo, store=store,
                    brain=Brain(FakeLLM(scripted=[]), cfg_demo),
                    rng=random.Random(0), clock=lambda: 1000.0, sleep=lambda _s: None)
        runner = TelethonRunner(
            client=_ClientDouble(_Sends()),
            personas={"demo": PersonaBundle(cfg=cfg_demo, deps=deps),
                      "demo2": PersonaBundle(cfg=cfg_second, deps=deps)},
            primary_slug="demo", allowlist=frozenset({PEER}),
            loop=asyncio.new_event_loop(), funnel_gate=False)
        got = runner._persona_settings("telegram:8849893367:demo2")
        assert got is cfg_second.settings, (
            "на contact_id 'telegram:8849893367:demo2' `_persona_settings` "
            "отдала настройки НЕ той персоны. Хвост причесали заодно с "
            "головой: `personas.get(slug, primary)` МОЛЧА свалился на primary, "
            "и карточка уйдёт владельцу на чужом языке.")
    finally:
        store.close()
