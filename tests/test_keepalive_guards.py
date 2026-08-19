# -*- coding: utf-8 -*-
"""Сторожа арки keep-alive кэша — К1–К7, К9, К10 + окно суток.

Источник истины — СПЕКА `docs/superpowers/specs/2026-08-09-chatter-cache-keepalive.md`
(§1 что пингуем, §2 чем, §3 частота и окно, §5 три жёстких правила, §6 N клиентов,
§9/§10 требования владельца) и КОНТРАКТ ведущей сессии на API модуля
`chatter.core.keepalive`. План реализации автору этих сторожей НЕ показывался —
правило проекта: тест и код не должны наследовать одно допущение.

К8 (детектор регрессии кэша под пингами) закрыт ШАГОМ 1 и живёт в
`tests/chatter/test_cache_health_keepalive.py`. Здесь он НЕ дублируется.

Класс дефекта, ради которого эти сторожа существуют
---------------------------------------------------
Keep-alive — фоновая оптимизация, у которой ВСЕ отказы тихие:

* пинг собрали «своей» сборкой префикса → он греет ЧУЖУЮ кэш-запись, боевая
  умирает по TTL, счёт растёт, а в логах ровно ноль изменений (К1, К2);
* пинг записался под боевым тегом → hit-rate и весь замер §4 испорчены изнутри,
  и сказать это некому: строки в `llm_usage` выглядят как боевые вызовы (К3);
* сбой фонового пинга посчитался боевым сбоем классификатора → порог 2/сутки
  поднимет алерт деградации на сетевую икоту фоновой задачи (К4);
* пинг ушёл поверх свежего боевого вызова → чистые деньги в никуда (К5);
* после `/reload` греется МЁРТВЫЙ префикс до 50 минут (К6);
* исключение на клиенте A убило цикл клиента B — и B молча остыл (К7);
* тумблер выключен, а что-то всё равно поменялось (К9);
* поле есть в дефолтах, но нет в списке известных ключей → клиент НЕ ПОДНИМЕТСЯ
  (К10; строгая проверка ключей уже стоит, см. `test_loader_rejects_unknown_keys`).

Ни один из этих отказов не виден ни лиду, ни владельцу. Поэтому сторожа —
единственное место, где они обязаны стать громкими.

Что здесь НЕ трогается: живые процессы, живые `.secrets/*.db`, живой конфиг.
Боевые `chatter/clients/*/settings.yaml` только ЧИТАЮТСЯ (К10) — иначе сторож
строгости ключей проверял бы строгость на выдуманных данных.

Допущения, которые спека оставила открытыми (названы вслух, а не спрятаны в
коде теста):

  A. **Часовой пояс окна суток.** `due_tags(window=(мин, мин))` — минуты от
     полуночи, но чьей. Спека говорит «окно суток КЛИЕНТА» и мотивирует ночным
     трафиком живых клиенток, то есть речь о человеческом времени суток.
     Сторожа читают окно в ЛОКАЛЬНОМ времени процесса. Если реализация решит
     считать в UTC — эти сторожа покраснеют, и это разногласие обязано быть
     разобранным, а не замазанным.
  B. **Строгое «>» на периоде.** §3 дословно: «пинговать префикс, только если
     с последнего РЕАЛЬНОГО вызова этого тега прошло **> 50 мин**». Ровно 3000 с
     — НЕ повод для пинга. Отдельный сторож, чтобы триаж был в одну строку.
  C. **Порог по объёму** (`min_dialogs_per_month`) в сторожах К1–К10, А и Б
     снят в 0, чтобы гейт объёма не маскировал проверяемое. САМ порог охраняет
     отдельный раздел В (ниже): и как поле конфига, и как ПОВЕДЕНИЕ.
  D. **`last_call_ts[tag] is None`** — вызова НЕ БЫЛО ВОВСЕ. §3 («прошло > 50
     мин с последнего вызова») формально этот случай накрывает, но §7 выносит
     «прогрев на старте раннера» ИЗ арки, а пинг по несуществующей записи — это
     ровно создание записи по ставке 1h ($6/M, ×20 к чтению). Развязано в
     пользу §7 отдельным сторожем
     `test_k5_never_called_prefix_is_not_warmed_from_scratch`; все остальные
     поведенческие сторожа стоят на клиенте С трафиком (`_seed_traffic`), чтобы
     этот спор не смешивался с проверяемым.
  E. **Граница порога объёма.** §3.1 п.4 говорит «ниже порога — ноль пингов» и
     про равенство молчит. Зафиксировано СТРОГО: `диалогов >= порога` →
     работаем, `<` → тишина (сторож `test_v3_volume_exactly_at_threshold_pings`,
     обоснование — в шапке раздела В). Тот же выбор уже сделан в
     `classifier_degraded`: со строгим `>` порог требует «порог плюс один», то
     есть молча становится другим числом.
"""
from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import logging
import random
from pathlib import Path

import pytest
import yaml

from chatter.config import loader as loader_mod
from chatter.config.loader import ConfigError, ControlConfig, load_config
from chatter.core.brain import build_system_prompt
from chatter.core.cache_health import CACHE_TTL_SEC, KEEPALIVE_TAG_PREFIX
from chatter.core.classifier import (
    classifier_failure_count, classifier_degraded, classifier_stable_prefix)
from chatter.core.llm import AnthropicLLM
from chatter.core.prefix_budget import classifier_model_of
from chatter.notify.base import Card, CardHandle, Notifier
from chatter.storage.db import Store
from chatter.telethon_run import PersonaBundle, keepalive_tick
from tests.chatter.test_loader import SETTINGS as _BASE_SETTINGS, _make_client

REPO = Path(__file__).resolve().parents[1]
CLIENTS = REPO / "chatter" / "clients"

# ЛИТЕРАЛЬНЫЕ списки. Выведенный интроспекцией список согласен с реализацией по
# определению: он примет ровно то, что она делает, и промолчит ровно там, где
# она забыла. Ниже — то, что требует СПЕКА, набранное руками.
KEEPALIVE_KEYS_LITERAL = {"enabled", "window", "min_dialogs_per_month"}
#
# Умолчание окна — 13 ч/сут (`09:00-22:00`), правка контракта от владельца
# 20.08. Спека §4/§10 п.5 считала экономику по 12 ч (`09:00-21:00`); новое
# значение — ОЦЕНКА до первого живого клиента, поэтому оно вписано сюда
# литералом, а не выведено из загрузчика: дефолт, «проверенный» самим собой,
# не проверен ничем, и следующая правка часа проедет молча.
KEEPALIVE_DEFAULTS_LITERAL = {"enabled": False,
                              "window": "09:00-22:00",
                              "min_dialogs_per_month": 25}
DEFAULT_WINDOW_HOURS_LITERAL = 13
COMBAT_TAGS_LITERAL = {"brain", "classifier", "classifier_retry"}
KEEPALIVE_TAGS_LITERAL = {"keepalive_brain", "keepalive_classifier"}
TAG_MAP_LITERAL = {"brain": "keepalive_brain", "classifier": "keepalive_classifier"}
PING_PERIOD_SEC_LITERAL = 3000          # §3: 50 минут

# Маркеры ИЗМЕНЧИВОГО хвоста (`system[1]`) — литералы из боевого кода.
# Их появление в кэшируемом блоке = регрессия 23.07 (изменчивое въехало в
# префикс, кэш умер на сутки при зелёных тестах).
VOLATILE_MARKERS = (
    "=== ПРОФИЛЬ КЛИЕНТА",          # classifier_volatile_suffix
    "=== ПОТОЧНИЙ ЧАС",             # brain.build_time_block
    "=== ВІДПОВІДЬ БОТА НА ЦЬОМУ ХОДУ",
    "=== ВІДКРИТІ ЗОБОВ'ЯЗАННЯ",
)


def _ka():
    """Модуль реализации. Импорт ЛЕНИВЫЙ и БЕЗ try/except.

    Пока реализации нет, каждый сторож падает своим `ImportError`, а файл при
    этом собирается — так видно, что красный от отсутствия кода, а не от
    опечатки в сторожах. Глушить импорт `except: pass` запрещено (DEV-18):
    проглоченный импорт превратил бы всю пачку в зелёную ширму.
    """
    import chatter.core.keepalive as module      # noqa: PLC0415
    return module


# ───────────────────────── эталон «боевого» префикса ─────────────────────────
#
# Собран ЗДЕСЬ, из боевых точек вызова (`brain.Brain.__init__`,
# `classifier.classify`, `prefix_budget.cache_threshold_verdict`), а НЕ взят из
# модуля keep-alive. Сторож, который сверяет реализацию с ней же самой, не
# сторожит ничего.

def _combat_prefix(cfg, tag: str) -> str:
    if tag == "brain":
        return build_system_prompt(cfg)
    if tag == "classifier":
        from chatter.run import obligations_slot_enabled   # единственный источник флага
        return classifier_stable_prefix(
            cfg.playbook, cfg.settings.language,
            cfg.settings.limits.profile_budget_tokens,
            track_obligations=obligations_slot_enabled())
    raise AssertionError(f"неизвестный боевой тег {tag!r}")


def _combat_model(cfg, tag: str) -> str:
    return cfg.settings.model if tag == "brain" else classifier_model_of(cfg)


# ───────────────────────────── фикстуры конфига ──────────────────────────────

KEEPALIVE_ON = ("\nkeepalive:\n"
                "  enabled: true\n"
                '  window: "00:00-23:59"\n'
                "  min_dialogs_per_month: 0\n")


def _client(root: Path, *, slug: str = "demo", extra: str = "",
            playbook: str = "Стадии воронки: знакомство, боль, оффер.",
            persona: str = "Меня зовут Аня, я консультант.",
            knowledge: str = "Консультация 5000 грн."):
    """Клиент во ВРЕМЕННОМ каталоге. Живой конфиг не читается и не пишется."""
    d = _make_client(root, slug, _BASE_SETTINGS + extra)
    (d / "playbook.md").write_text(playbook, encoding="utf-8")
    (d / "persona.md").write_text(persona, encoding="utf-8")
    (d / "knowledge.md").write_text(knowledge, encoding="utf-8")
    return load_config(root, slug)


@pytest.fixture()
def flags(monkeypatch):
    """Оба env-флага, от которых зависит ФОРМА боевого префикса, прибиты явно.

    Иначе сторож сверял бы две строки, собранные в разных режимах, и краснел бы
    от окружения, а не от дефекта.
    """
    monkeypatch.setenv("CHATTER_CLASSIFIER_CACHE", "1")
    monkeypatch.setenv("CHATTER_OBLIGATIONS_SLOT", "0")


def _store(tmp_path: Path) -> Store:
    """БД во временном каталоге. Живые `.secrets/*.db` не открываются никогда."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    return Store(tmp_path / "guard.db")


def _seed_traffic(store: Store, *, now: float, ago: float = 2 * 3600.0) -> Store:
    """Боевые вызовы ОБОИХ префиксов, давно (см. допущение D).

    Пустая база — это клиент, у которого кэш-записи нет вовсе; поведение в этом
    случае спорное и разбирается отдельным сторожем. Все поведенческие сторожа
    ниже стоят на НОРМАЛЬНОМ клиенте: трафик был, префикс остыл, пора греть.
    """
    for tag in ("brain", "classifier"):
        store.add_llm_usage(tag=tag, model="claude-sonnet-5", input_tokens=100,
                            output_tokens=10, cache_read_input_tokens=8000,
                            cache_creation_input_tokens=0, ts=now - ago)
    return store


# ─────────────────────────────── шпион вместо LLM ────────────────────────────

class _MessagesShim:
    def __init__(self, owner):
        self._owner = owner

    def create(self, **kwargs):
        self._owner.sdk_calls.append(kwargs)
        blocks = kwargs.get("system") or []
        text = blocks[0].get("text", "") if blocks and isinstance(blocks[0], dict) else ""
        self._owner._maybe_fail(kwargs.get("tag", ""), text)
        return _FakeResponse(self._owner.cache_read)


class _SDKShim:
    def __init__(self, owner):
        self.messages = _MessagesShim(owner)


class _FakeUsage:
    def __init__(self, cache_read: int):
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read_input_tokens = cache_read
        self.cache_creation_input_tokens = 0
        self.cache_creation = None


class _FakeResponse:
    def __init__(self, cache_read: int):
        self.content = []
        self.stop_reason = "max_tokens"
        self.usage = _FakeUsage(cache_read)


class SpyLLM:
    """Записывает ВСЁ, чем его дёрнули, и умеет падать по требованию.

    Наблюдаемые пути перечислены ЯВНО, без `__getattr__`-магии:
      * `send_raw(payload, tag=...)` — шов пинга (`chatter/core/llm.py`);
      * `complete(...)` — интерфейс `LLMClient` (боевой путь brain/classifier);
      * `_client.messages.create(**payload)` — сырой SDK.
    Если цикл дёрнул что-то четвёртое, сторож скажет об этом прямым текстом, а
    не зазеленеет от того, что ничего не увидел.

    ⚠️ Шов `send_raw` в КОНТРАКТЕ ведущей сессии не назван — контракт описывает
    только `build_ping_payload`/`run_keepalive_cycle(cfg, store, llm, …)` и
    молчит о том, каким методом цикл разговаривает с LLM. Форма взята из
    ПУБЛИЧНОГО `chatter/core/llm.py` (`LLMClient`/`FakeLLM`/`AnthropicLLM`), а
    не из модуля keep-alive: сторожу нужен двойник СОБЕСЕДНИКА, и брать его
    форму больше неоткуда. Это пробел контракта, а не сторожа.
    """

    def __init__(self, *, fail_all: bool = False, fail_tags=(),
                 fail_markers=(), cache_read: int = 5000):
        self.calls: list[dict] = []
        self.sdk_calls: list[dict] = []
        self.pings: list[dict] = []
        self.fail_all = fail_all
        self.fail_tags = set(fail_tags)
        self.fail_markers = tuple(fail_markers)
        self.cache_read = cache_read
        self.last_stop_reason = None
        self._client = _SDKShim(self)
        self.messages = self._client.messages

    def _maybe_fail(self, tag: str, system_text: str) -> None:
        if self.fail_all or (tag and tag in self.fail_tags) or any(
                m in system_text for m in self.fail_markers):
            raise RuntimeError(f"пинг {tag or '?'}: транспорт лёг (сымитировано сторожем)")

    def complete(self, system, messages, *, max_tokens, no_thinking=False,
                 uncached_suffix=None, tag=""):
        self.calls.append({"system": system, "messages": messages,
                           "max_tokens": max_tokens, "no_thinking": no_thinking,
                           "uncached_suffix": uncached_suffix, "tag": tag})
        self.last_stop_reason = "max_tokens"
        self._maybe_fail(tag, system if isinstance(system, str) else "")
        return ""

    def send_raw(self, payload: dict, *, tag: str) -> dict:
        """Шов пинга. Возвращает строку usage той же формы, что боевой клиент."""
        self.pings.append({"payload": payload, "tag": tag})
        blocks = payload.get("system") or []
        text = blocks[0].get("text", "") if blocks and isinstance(blocks[0], dict) else ""
        self._maybe_fail(tag, text)
        return {"tag": tag, "model": payload.get("model", ""),
                "input_tokens": 0, "output_tokens": 0,
                "cache_read_input_tokens": int(self.cache_read),
                "cache_creation_input_tokens": 0,
                "cache_creation_5m": 0, "cache_creation_1h": 0}


def _blocks_of(payload: dict) -> list:
    return payload.get("system") or []


def _systems_seen(spy: SpyLLM) -> list[str]:
    """Тексты кэшируемого блока, реально ушедшие в модель, ВСЕМИ путями."""
    out = [c["system"] for c in spy.calls if isinstance(c["system"], str)]
    for kw in list(spy.sdk_calls) + [p["payload"] for p in spy.pings]:
        blocks = _blocks_of(kw)
        if blocks and isinstance(blocks[0], dict):
            out.append(blocks[0].get("text", ""))
    return out


def _usage_calls(store: Store) -> dict[str, int]:
    return {tag: row["calls"] for tag, row in store.llm_usage_totals().items()}


def _tags_seen(spy: SpyLLM, store: Store, before: dict[str, int]) -> set[str]:
    """Теги, под которыми вызов стал наблюдаемым: у LLM или НОВОЙ строкой в
    `llm_usage`. Сравнение с `before` обязательно — в базе уже лежит боевой
    трафик, и без него сторож обвинил бы пинг в чужих строках."""
    tags = {c["tag"] for c in spy.calls if c["tag"]}
    tags |= {p["tag"] for p in spy.pings if p["tag"]}
    tags |= {kw["tag"] for kw in spy.sdk_calls if kw.get("tag")}
    after = _usage_calls(store)
    tags |= {tag for tag, n in after.items() if n > before.get(tag, 0)}
    return tags


def _cycle(cfg, store, llm, *, now, notifier=None):
    """Один проход. `notifier` передаётся ТОЛЬКО когда сторож его проверяет —
    иначе сторожа, написанные до доставки алерта, поехали бы на новом kwarg'е.

    ⚠️ Имя шва (`notifier=`) в контракте ведущей сессии не названо: контракт
    описывает `run_keepalive_cycle(cfg, store, llm, *, now)` и молчит о том,
    чем цикл разговаривает с владельцем. Взято по §5 («доставка — тем же путём,
    что `_maybe_degraded_alert`», а тот получает `deps.notifier`). Пробел
    контракта, а не сторожа.
    """
    kwargs = {"now": now}
    if notifier is not None:
        kwargs["notifier"] = notifier
    return asyncio.run(_ka().run_keepalive_cycle(cfg, store, llm, **kwargs))


# ───────────────────────────── время суток (локально) ────────────────────────

def _at(hour: int, minute: int = 0, day: int = 20) -> float:
    """Момент 2026-08-{day} hh:mm по ЛОКАЛЬНОМУ времени (см. допущение A)."""
    return dt.datetime(2026, 8, day, hour, minute).timestamp()


def _win(start: str, end: str) -> tuple[int, int]:
    def m(v: str) -> int:
        h, mm = v.split(":")
        return int(h) * 60 + int(mm)
    return m(start), m(end)


# ═════════════════════════ К1. Префикс строит БОЕВОЙ код ═════════════════════
#
# Главный сторож арки. Своя сборка префикса — не стилистика: пинг с чужим
# байтом греет ДРУГУЮ кэш-запись. Боевая при этом умирает по TTL ровно как
# раньше, счёт растёт, а метрика «пинги идут» зелёная. Отличить это от
# работающей арки можно только побайтовой сверкой.

def test_k1_brain_ping_prefix_is_byte_identical_to_the_combat_prefix(tmp_path, flags):
    cfg = _client(tmp_path / "a")
    payload = _ka().build_ping_payload(cfg, "brain")
    got = payload["system"][0]["text"]
    assert got.encode("utf-8") == _combat_prefix(cfg, "brain").encode("utf-8"), (
        "system[0] пинга brain разошёлся с боевым префиксом — пинг греет ЧУЖУЮ "
        "кэш-запись, боевая умирает по TTL, а метрики молчат")


def test_k1_classifier_ping_prefix_is_byte_identical_to_the_combat_prefix(tmp_path, flags):
    cfg = _client(tmp_path / "a")
    payload = _ka().build_ping_payload(cfg, "classifier")
    got = payload["system"][0]["text"]
    assert got.encode("utf-8") == _combat_prefix(cfg, "classifier").encode("utf-8")


def test_k1_classifier_prefix_follows_the_obligations_slot_flag(tmp_path, monkeypatch):
    """Форма боевого префикса зависит от `CHATTER_OBLIGATIONS_SLOT` (~900 ток).

    Пинг, собранный в ДРУГОЙ форме, — это ровно тот же промах мимо кэша, только
    его не видно даже побайтовой сверкой в одном режиме. Поэтому сверяем в
    ОБОИХ и заодно доказываем, что сторож не вырожденный: формы обязаны
    отличаться друг от друга.
    """
    monkeypatch.setenv("CHATTER_CLASSIFIER_CACHE", "1")
    cfg = _client(tmp_path / "a")

    monkeypatch.setenv("CHATTER_OBLIGATIONS_SLOT", "0")
    off_ping = _ka().build_ping_payload(cfg, "classifier")["system"][0]["text"]
    off_combat = _combat_prefix(cfg, "classifier")

    monkeypatch.setenv("CHATTER_OBLIGATIONS_SLOT", "1")
    on_ping = _ka().build_ping_payload(cfg, "classifier")["system"][0]["text"]
    on_combat = _combat_prefix(cfg, "classifier")

    assert off_combat != on_combat, (
        "сторож вырожден: боевой префикс не зависит от флага слота — проверять "
        "нечего, разберись ДО того, как признавать К1 зелёным")
    assert off_ping.encode("utf-8") == off_combat.encode("utf-8")
    assert on_ping.encode("utf-8") == on_combat.encode("utf-8")


def test_k1_ping_carries_the_same_model_and_the_cache_breakpoint(tmp_path, flags):
    """Модель та же (иначе другая кэш-запись) и брейкпоинт тот же (иначе кэша
    вообще нет). `ttl: 1h` — литерал из боевого `AnthropicLLM.complete`."""
    cfg = _client(tmp_path / "a")
    for tag in ("brain", "classifier"):
        payload = _ka().build_ping_payload(cfg, tag)
        assert payload["model"] == _combat_model(cfg, tag), tag
        assert payload["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}, tag
        assert payload["max_tokens"] == 0, f"{tag}: §2 — прогрев без тарификации выхода"
        assert payload["messages"], f"{tag}: минимальная заглушка обязана быть непустой"


def test_k1_ping_prefix_tracks_the_client_files_not_a_snapshot(tmp_path, flags):
    """Два разных клиента — два разных префикса, каждый равен СВОЕМУ боевому.

    Ловит захардкоженный/закэшированный на импорте текст: он был бы равен
    боевому у одного клиента и молча грел бы чужую запись у второго.
    """
    a = _client(tmp_path / "a", slug="alpha", persona="Меня зовут Аня.",
                playbook="ПЛЕЙБУК АЛЬФА")
    b = _client(tmp_path / "b", slug="beta", persona="Меня зовут Оля.",
                playbook="ПЛЕЙБУК БЕТА")
    for tag in ("brain", "classifier"):
        ta = _ka().build_ping_payload(a, tag)["system"][0]["text"]
        tb = _ka().build_ping_payload(b, tag)["system"][0]["text"]
        assert ta != tb, f"{tag}: префикс не зависит от клиента"
        assert ta == _combat_prefix(a, tag) and tb == _combat_prefix(b, tag)


# ═════════════════════ К2. Изменчивого хвоста в пинге НЕТ ════════════════════
#
# `system[1]` — то, что меняется каждый ход. Приехав в пинг, он либо сдвинет
# брейкпоинт, либо (что хуже) окажется ВНУТРИ кэшируемого блока — это и есть
# регрессия 23.07, прожившая сутки незамеченной.

def test_k2_ping_has_exactly_one_system_block(tmp_path, flags):
    cfg = _client(tmp_path / "a")
    for tag in ("brain", "classifier"):
        blocks = _ka().build_ping_payload(cfg, tag)["system"]
        assert isinstance(blocks, list), tag
        assert len(blocks) == 1, (
            f"{tag}: в пинге {len(blocks)} system-блоков — изменчивый хвост "
            f"не имеет права ехать в прогрев (§2)")


def test_k2_no_volatile_marker_leaked_into_the_cached_block(tmp_path, flags):
    """Ноль блоков — не то же самое, что «хвост не приехал»: его могли
    приклеить К ПЕРВОМУ блоку. Сторож стоит на маркерах самого текста."""
    cfg = _client(tmp_path / "a")
    for tag in ("brain", "classifier"):
        text = _ka().build_ping_payload(cfg, tag)["system"][0]["text"]
        for marker in VOLATILE_MARKERS:
            assert marker not in text, (
                f"{tag}: изменчивый блок {marker!r} въехал в КЭШИРУЕМЫЙ префикс "
                f"— это регрессия 23.07, кэш умрёт молча")


def test_k2_cycle_sends_no_volatile_tail_either(tmp_path, flags):
    """То же самое, но на том, что РЕАЛЬНО ушло в модель: `build_ping_payload`
    может быть чист, а цикл — дописать хвост от себя."""
    cfg = _client(tmp_path / "a", extra=KEEPALIVE_ON)
    now = _at(12)
    spy, store = SpyLLM(), _seed_traffic(_store(tmp_path), now=now)
    _cycle(cfg, store, spy, now=now)
    seen = _systems_seen(spy)
    assert seen, "цикл не сделал ни одного наблюдаемого вызова — проверять нечего"
    for text in seen:
        for marker in VOLATILE_MARKERS:
            assert marker not in text
    for call in spy.calls:
        assert not call["uncached_suffix"], (
            "цикл передал uncached_suffix — это второй system-блок, §2 его запрещает")
    for p in spy.pings:
        assert len(_blocks_of(p["payload"])) == 1, (
            f"цикл послал {len(_blocks_of(p['payload']))} system-блоков вместо одного")


# ═══════════════ К3. Пинг пишется под СВОИМ тегом, не под боевым ═════════════
#
# §5 правило 3: под тегом `classifier` пинги изнутри неотличимы от боевых
# вызовов и мгновенно портят и hit-rate, и весь замер §4. Испорченный замер —
# это не «неточность»: по нему принимается решение включать/откатывать арку.

def test_k3_tag_map_is_exactly_the_two_documented_pairs():
    """Литеральное равенство словаря целиком — сразу в обе стороны."""
    assert _ka().TAG_MAP == TAG_MAP_LITERAL


def test_k3_keepalive_tags_are_disjoint_from_combat_tags():
    values = set(_ka().TAG_MAP.values())
    assert values == KEEPALIVE_TAGS_LITERAL
    assert values & COMBAT_TAGS_LITERAL == set(), (
        "тег пинга совпал с боевым — замер §4 и hit-rate испорчены изнутри")
    for v in values:
        assert v.startswith(KEEPALIVE_TAG_PREFIX), (
            f"{v!r} не начинается с {KEEPALIVE_TAG_PREFIX!r} — детектор регрессии "
            f"кэша (шаг 1, `cache_health.keepalive_touches`) его не увидит")


def test_k3_ping_results_carry_the_combat_tag_not_the_keepalive_one(tmp_path, flags):
    """Контракт: `PingResult.tag` — БОЕВОЙ тег. Иначе вызывающая сторона будет
    сопоставлять пинг с боевым префиксом по разным ключам."""
    cfg = _client(tmp_path / "a", extra=KEEPALIVE_ON)
    now = _at(12)
    store = _seed_traffic(_store(tmp_path), now=now)
    results = _cycle(cfg, store, SpyLLM(), now=now)
    assert {r.tag for r in results} == set(TAG_MAP_LITERAL), (
        f"цикл вернул теги {sorted(r.tag for r in results)}, ожидались боевые "
        f"{sorted(TAG_MAP_LITERAL)} — оба префикса клиента обязаны быть в проходе (§1)")


def test_k3_usage_of_a_ping_is_recorded_under_keepalive_tag_only(tmp_path, flags):
    cfg = _client(tmp_path / "a", extra=KEEPALIVE_ON)
    now = _at(12)
    spy, store = SpyLLM(), _seed_traffic(_store(tmp_path), now=now)
    before = _usage_calls(store)
    _cycle(cfg, store, spy, now=now)

    tags = _tags_seen(spy, store, before)
    assert tags, ("ни LLM, ни llm_usage не увидели ни одного тега — либо цикл "
                  "ничего не послал, либо послал третьим путём; и то и другое "
                  "разбирается, а не засчитывается зелёным")
    assert tags & COMBAT_TAGS_LITERAL == set(), (
        f"пинг ушёл под боевым тегом: {sorted(tags & COMBAT_TAGS_LITERAL)} — "
        f"hit-rate и весь замер §4 испорчены изнутри")
    assert tags == KEEPALIVE_TAGS_LITERAL, f"неожиданные теги пингов: {sorted(tags)}"

    after = _usage_calls(store)
    for combat in sorted(COMBAT_TAGS_LITERAL):
        assert after.get(combat, 0) == before.get(combat, 0), (
            f"пинг дописал строку под боевым тегом {combat!r}")


# ═════════ К4. Сбой пинга не трогает лида и не считается сбоем боя ═══════════
#
# §5 правила 1 и 2. Порог `classifier_error_threshold` = 2/сутки. Если туда
# потекут сетевые икоты фоновой задачи, порог сработает не на том и его
# поднимут — а вместе с ним ослепнет настоящий сигнал деградации.

def test_k4_ping_failure_does_not_escape_the_cycle(tmp_path, flags):
    cfg = _client(tmp_path / "a", extra=KEEPALIVE_ON)
    now = _at(12)
    spy, store = SpyLLM(fail_all=True), _seed_traffic(_store(tmp_path), now=now)
    results = _cycle(cfg, store, spy, now=now)         # не бросает — иначе красный здесь
    assert results, "при отказе цикл обязан вернуть результаты, а не пустоту"
    for r in results:
        assert r.sent is False, f"{r.tag}: отказавший пинг помечен отправленным"
        assert r.error, f"{r.tag}: отказ без текста ошибки = тихо проглоченный сбой (DEV-18)"


def test_k4_ping_failure_is_not_counted_as_a_classifier_failure(tmp_path, flags):
    """Считаем ТЕМ ЖЕ счётчиком, что и боевой алерт (`chatter/run.py:752`),
    а не своей арифметикой — иначе сторож проверял бы вымышленный порог."""
    cfg = _client(tmp_path / "a", extra=KEEPALIVE_ON)
    now = _at(12)
    store = _seed_traffic(_store(tmp_path), now=now)
    _cycle(cfg, store, SpyLLM(fail_all=True), now=now)

    assert store.count_events("classifier_error", since_ts=0) == 0
    assert store.count_events("classifier_recovered", since_ts=0) == 0
    assert classifier_failure_count(store, now=now, window_seconds=86400.0) == 0, (
        "сбой ФОНОВОГО пинга попал в счётчик сбоев классификатора — сетевая "
        "икота keep-alive поднимет боевой алерт деградации (§5 правило 2)")
    assert classifier_degraded(store, now=now, window_seconds=86400.0, threshold=2) is False


def test_k4_repeated_ping_failures_still_do_not_reach_the_threshold(tmp_path, flags):
    """Порог = 2. Один сбой мог не дотянуть до него случайно; берём с запасом."""
    cfg = _client(tmp_path / "a", extra=KEEPALIVE_ON)
    base = _at(6)
    store = _seed_traffic(_store(tmp_path), now=base)
    for i in range(4):
        _cycle(cfg, store, SpyLLM(fail_all=True), now=base + i * 3600.0)
    now = base + 4 * 3600.0
    assert classifier_degraded(store, now=now, window_seconds=86400.0, threshold=2) is False


# ═════════════ К5. Свежий боевой вызов отменяет пинг (§3, деньги) ════════════

def test_k5_period_constant_is_fifty_minutes():
    assert _ka().PING_PERIOD_SEC == PING_PERIOD_SEC_LITERAL


def test_k5_tag_called_recently_is_not_due():
    now = _at(12)
    due = _ka().due_tags(now=now,
                         last_call_ts={"brain": now - 49 * 60,
                                       "classifier": now - 2 * 3600.0})
    assert "brain" not in due, (
        "пинг ушёл поверх боевого вызова 49-минутной давности — чистая трата "
        "(§3: живой ход сам продлевает TTL)")
    assert "classifier" in due, "молчащий два часа тег обязан быть в очереди"


def test_k5_never_called_prefix_is_not_warmed_from_scratch():
    """`None` — записи кэша НЕТ вовсе. Пинг по ней не продлевает, а СОЗДАЁТ её
    по ставке записи ($6/M, ×20 к чтению), и это ровно «прогрев на старте
    раннера», который §7 выносит ИЗ арки: «Прогрев на старте раннера — соседняя
    задача, другой триггер, отдельный замер».

    Место спорное (см. шапку, допущение D): §3 формулирует правило как «прошло
    > 50 мин с последнего вызова», и «вызова не было вовсе» под эту формулу
    формально подходит. Развязано в пользу §7: у тихого клиента, где записи нет
    именно из-за тишины, обратное чтение означало бы платить ставку записи
    каждый час без единого лида — тот самый случай, про который §4 говорит, что
    арка ему не окупается.
    """
    now = _at(12)
    assert "brain" not in _ka().due_tags(now=now, last_call_ts={"brain": None})


def test_k5_tag_silent_longer_than_the_period_is_due():
    now = _at(12)
    due = _ka().due_tags(now=now, last_call_ts={"brain": now - 51 * 60})
    assert "brain" in due


def test_k5_exact_period_boundary_is_not_due():
    """Допущение B: §3 говорит «> 50 мин». Ровно 3000 с — ещё не повод."""
    now = _at(12)
    due = _ka().due_tags(now=now, last_call_ts={"brain": now - 3000.0})
    assert "brain" not in due


def test_k5_period_is_a_parameter_not_a_hardcoded_literal():
    now = _at(12)
    assert "brain" in _ka().due_tags(now=now, last_call_ts={"brain": now - 700.0},
                                     period_sec=600)
    assert "brain" not in _ka().due_tags(now=now, last_call_ts={"brain": now - 700.0},
                                         period_sec=1200)


def test_k5_tags_are_judged_independently():
    """Один префикс греется живым трафиком, второй молчит — это НОРМАЛЬНОЕ
    состояние клиента, и оно не должно ни глушить, ни будить второй тег."""
    now = _at(12)
    due = _ka().due_tags(now=now,
                         last_call_ts={"brain": now - 60.0, "classifier": now - 7200.0})
    assert set(due) == {"classifier"}


# ═════════════════ ОКНО СУТОК — из конфига, не из константы ══════════════════
#
# §3/§9 п.1/§10 п.5: «окно суток — параметр клиента, не константа». Умолчание
# спека считала 12 ч/сут, владелец 20.08 поправил на 13 ч (`09:00-22:00`) —
# ОЦЕНКА до первого живого клиента. Константа в коде здесь стоит вдвое дороже:
# 24/7 — двукратная стоимость режима (§4), а у клиента с ночными лидами окно
# своё. Числа §4 (безубыток 13.2 диал/мес) посчитаны на 12 ч и с новым
# умолчанием слегка расходятся — это ЗАМЕЧЕНО, а не спрятано.

def test_window_default_is_the_thirteen_hour_day_window(tmp_path):
    """Умолчание — ЛИТЕРАЛ `09:00-22:00` (правка контракта 20.08, 13 ч/сут).

    Проверяется и строка, и её длительность: строку можно поправить в одном
    месте и забыть, что от неё считается стоимость режима (§4 — окно входит в
    цену линейно, 24/7 против 12ч это двукратная разница).
    """
    cfg = _client(tmp_path / "a")           # блока keepalive в settings.yaml НЕТ
    assert cfg.settings.keepalive.window == KEEPALIVE_DEFAULTS_LITERAL["window"]
    start, end = _win(*KEEPALIVE_DEFAULTS_LITERAL["window"].split("-"))
    assert end - start == DEFAULT_WINDOW_HOURS_LITERAL * 60, (
        f"умолчание окна обязано быть ровно {DEFAULT_WINDOW_HOURS_LITERAL} ч/сут")


def _stale(now: float) -> dict[str, float]:
    """Тег, молчавший два часа: обе стороны спора о `None` (допущение D)
    согласны, что он ПОРА. Окно суток проверяется на нём, чтобы не смешивать
    два вопроса в одном красном."""
    return {"brain": now - 2 * 3600.0}


def test_window_none_means_no_restriction():
    now = _at(3, 30)
    assert "brain" in _ka().due_tags(now=now, last_call_ts=_stale(now), window=None)


def test_window_outside_hours_suppresses_the_ping():
    now = _at(12)
    inside = _ka().due_tags(now=now, last_call_ts=_stale(now), window=_win("09:00", "21:00"))
    outside = _ka().due_tags(now=now, last_call_ts=_stale(now), window=_win("00:00", "05:00"))
    assert "brain" in inside
    assert "brain" not in outside, (
        "пинг ушёл ВНЕ окна суток — это ровно те деньги, ради экономии которых "
        "умолчание сделали 12ч, а не 24/7 (см. допущение A про часовой пояс)")


def test_window_edges_are_inclusive_start_exclusive_end():
    """Границы названы явно: без этого «09:00-21:00» у двух читателей значит
    разное, и расхождение всплывёт как лишний платный пинг раз в сутки."""
    w = _win("09:00", "21:00")
    for hh, mm, want in ((9, 0, True), (20, 59, True), (21, 0, False), (8, 59, False)):
        now = _at(hh, mm)
        due = _ka().due_tags(now=now, last_call_ts=_stale(now), window=w)
        assert ("brain" in due) is want, f"{hh:02d}:{mm:02d} в окне 09:00-21:00 → {want}"


def test_window_is_taken_from_the_client_config(tmp_path, flags):
    """Два клиента, одно и то же «сейчас», РАЗНЫЕ окна — разное поведение.

    Ловит константу в горячем пути: с ней оба клиента вели бы себя одинаково.
    """
    night = _client(tmp_path / "n", slug="night",
                    extra=("\nkeepalive:\n  enabled: true\n"
                           '  window: "22:00-23:59"\n  min_dialogs_per_month: 0\n'))
    day = _client(tmp_path / "d", slug="day",
                  extra=("\nkeepalive:\n  enabled: true\n"
                         '  window: "06:00-18:00"\n  min_dialogs_per_month: 0\n'))
    noon = _at(12)

    spy_n, spy_d = SpyLLM(), SpyLLM()
    res_n = _cycle(night, _seed_traffic(_store(tmp_path / "sn"), now=noon), spy_n, now=noon)
    res_d = _cycle(day, _seed_traffic(_store(tmp_path / "sd"), now=noon), spy_d, now=noon)

    assert not any(r.sent for r in res_n) and not _systems_seen(spy_n), (
        "клиент с ночным окном пингнул в полдень — окно взято не из его конфига")
    assert any(r.sent for r in res_d) and _systems_seen(spy_d)


@pytest.mark.parametrize("bad", ["9-21", "09:00", "abc", "25:00-26:00",
                                 "09:60-21:00", "21:00", ""])
def test_window_malformed_value_is_a_loud_config_error(tmp_path, bad):
    """Кривое окно → ГРОМКИЙ отказ, а не молчаливый дефолт: молчаливый дефолт
    даёт клиента, который «настроен» на одно, а работает по другому."""
    root = tmp_path / str(abs(hash(bad)))
    with pytest.raises(ConfigError) as e:
        _client(root, extra=f'\nkeepalive:\n  enabled: true\n  window: "{bad}"\n')
    assert "window" in str(e.value), f"отказ обязан НАЗВАТЬ поле: {e.value}"


# ═════════════ К6. `/reload` — перестать греть МЁРТВЫЙ префикс ═══════════════
#
# §6 п.2: смена плейбука/персоны меняет префикс, старая кэш-запись мертва.
# Греть её — до 50 минут платных холостых пингов после КАЖДОГО `/reload`.

def test_k6_payload_follows_the_reloaded_config(tmp_path, flags):
    before = _client(tmp_path / "v1", slug="c", playbook="ПЛЕЙБУК ВЕРСИЯ ОДИН")
    after = _client(tmp_path / "v2", slug="c", playbook="ПЛЕЙБУК ВЕРСИЯ ДВА")
    for tag in ("brain", "classifier"):
        t_before = _ka().build_ping_payload(before, tag)["system"][0]["text"]
        t_after = _ka().build_ping_payload(after, tag)["system"][0]["text"]
        assert t_before != t_after, f"{tag}: пинг не заметил смены конфига"
        assert t_after == _combat_prefix(after, tag)
        assert "ВЕРСИЯ ОДИН" not in t_after, (
            f"{tag}: греется МЁРТВЫЙ префикс — текст из конфига ДО /reload")


def test_k6_cycle_stops_warming_the_dead_prefix(tmp_path, flags):
    """Тот же вопрос на уровне цикла — ловит мемоизацию префикса в модуле
    (`lru_cache`, глобаль, поле объекта): она пережила бы `/reload` молча."""
    v1 = _client(tmp_path / "v1", slug="c", extra=KEEPALIVE_ON,
                 playbook="ПЛЕЙБУК ВЕРСИЯ ОДИН")
    v2 = _client(tmp_path / "v2", slug="c", extra=KEEPALIVE_ON,
                 playbook="ПЛЕЙБУК ВЕРСИЯ ДВА")
    first = _at(10)
    store, spy = _seed_traffic(_store(tmp_path), now=first), SpyLLM()
    _cycle(v1, store, spy, now=first)
    before_count = len(_systems_seen(spy))

    _cycle(v2, store, spy, now=first + 2 * 3600.0)      # период заведомо истёк
    after = _systems_seen(spy)[before_count:]
    assert after, "после /reload цикл не пингнул вовсе"
    for text in after:
        assert "ВЕРСИЯ ДВА" in text or "ВЕРСИЯ ОДИН" not in text
        assert "ВЕРСИЯ ОДИН" not in text, (
            "после /reload пинг продолжил греть префикс СТАРОЙ версии конфига")


# ═════════ К7. Отказ одного клиента не глушит остальных (§6 п.4) ═════════════

def test_k7_failure_of_client_a_does_not_break_client_b(tmp_path, flags):
    """Исключение, вылетевшее наружу, обрывает ОБЩИЙ проход по клиентам — и
    остывает не тот клиент, у которого проблема."""
    a = _client(tmp_path / "a", slug="alpha", extra=KEEPALIVE_ON, persona="Аня А.")
    b = _client(tmp_path / "b", slug="beta", extra=KEEPALIVE_ON, persona="Оля Б.")
    now = _at(12)

    spy_a, spy_b = SpyLLM(fail_all=True), SpyLLM()
    res_a = _cycle(a, _seed_traffic(_store(tmp_path / "sa"), now=now), spy_a, now=now)
    res_b = _cycle(b, _seed_traffic(_store(tmp_path / "sb"), now=now), spy_b, now=now)

    assert res_a and not any(r.sent for r in res_a)
    assert res_b and all(r.sent for r in res_b), "клиент B остался без пингов"
    assert all(r.error is None for r in res_b)


def test_k7_failure_of_one_prefix_does_not_skip_the_other(tmp_path, flags):
    """Тот же класс внутри одного клиента: префиксов ДВА (§1), и упавший brain
    не имеет права унести с собой classifier."""
    cfg = _client(tmp_path / "a", extra=KEEPALIVE_ON,
                  persona="УНИКАЛЬНЫЙ МАРКЕР ПЕРСОНЫ ДЛЯ ОТКАЗА")
    now = _at(12)
    spy = SpyLLM(fail_tags={"keepalive_brain"},
                 fail_markers=("УНИКАЛЬНЫЙ МАРКЕР ПЕРСОНЫ ДЛЯ ОТКАЗА",))
    store = _seed_traffic(_store(tmp_path), now=now)
    results = {r.tag: r for r in _cycle(cfg, store, spy, now=now)}
    assert set(results) == set(TAG_MAP_LITERAL)
    assert results["brain"].sent is False and results["brain"].error
    assert results["classifier"].sent is True, (
        "отказ brain унёс с собой classifier — второй префикс остыл из-за первого")


# ═══════════ К9. Тумблер выключен → байт-в-байт прежнее поведение ════════════

def test_k9_disabled_is_the_default(tmp_path):
    cfg = _client(tmp_path / "a")
    assert cfg.settings.keepalive.enabled is False, (
        "keep-alive включился сам: §6 п.1 — на тихом клиенте это прямой убыток")


def test_k9_disabled_client_sends_zero_pings(tmp_path, flags):
    cfg = _client(tmp_path / "a",
                  extra=("\nkeepalive:\n  enabled: false\n"
                         '  window: "00:00-23:59"\n  min_dialogs_per_month: 0\n'))
    now = _at(12)
    spy, store = SpyLLM(), _seed_traffic(_store(tmp_path), now=now)
    before = _usage_calls(store)
    results = _cycle(cfg, store, spy, now=now)
    assert results == [], f"выключенный тумблер вернул результаты: {results}"
    assert spy.calls == [] and spy.sdk_calls == [] and spy.pings == []
    assert _usage_calls(store) == before, "выключенный тумблер дописал строку в llm_usage"


def test_k9_default_client_without_the_block_sends_zero_pings(tmp_path, flags):
    """Клиент, у которого блока `keepalive` в settings.yaml вовсе нет, обязан
    жить ровно как до арки."""
    cfg = _client(tmp_path / "a")
    now = _at(12)
    spy, store = SpyLLM(), _seed_traffic(_store(tmp_path), now=now)
    assert _cycle(cfg, store, spy, now=now) == []
    assert spy.calls == [] and spy.sdk_calls == [] and spy.pings == []


def test_k9_keepalive_block_does_not_change_the_combat_prompts(tmp_path, flags):
    """«Байт-в-байт прежнее» — про то, что уходит в модель. Настройка фоновой
    оптимизации не имеет права поменять ни байта боевого префикса."""
    without = _client(tmp_path / "w", slug="c")
    with_block = _client(tmp_path / "k", slug="c", extra=KEEPALIVE_ON)
    for tag in ("brain", "classifier"):
        assert (_combat_prefix(without, tag).encode("utf-8")
                == _combat_prefix(with_block, tag).encode("utf-8")), tag


# ═══════ К10. Ключи конфига: три поля, оба списка, ВСЕ боевые клиенты ════════
#
# Строгая проверка ключей уже стоит (`_reject_unknown`). Значит поле, попавшее
# в дефолты и НЕ попавшее в список известных, — это клиент, который не
# поднимется, и гардиан в шторме рестартов. Поэтому оба списка сверяются с
# ЛИТЕРАЛЬНЫМ третьим и с боевыми файлами.

def test_k10_keepalive_is_a_known_top_level_key():
    assert "keepalive" in loader_mod._SETTINGS_KEYS


def test_k10_keepalive_keys_match_the_literal_list_both_ways():
    known = set(loader_mod._KEEPALIVE_KEYS)
    assert known - KEEPALIVE_KEYS_LITERAL == set(), (
        f"в _KEEPALIVE_KEYS есть поля, которых спека не вводила: "
        f"{sorted(known - KEEPALIVE_KEYS_LITERAL)}")
    assert KEEPALIVE_KEYS_LITERAL - known == set(), (
        f"поля спеки не попали в _KEEPALIVE_KEYS: "
        f"{sorted(KEEPALIVE_KEYS_LITERAL - known)} — клиент с ними НЕ ПОДНИМЕТСЯ")


def test_k10_config_object_fields_match_the_literal_list_both_ways(tmp_path):
    cfg = _client(tmp_path / "a")
    fields = {f.name for f in dataclasses.fields(type(cfg.settings.keepalive))}
    assert fields == KEEPALIVE_KEYS_LITERAL, (
        f"поля объекта настроек и список известных ключей разошлись: {sorted(fields)}")


def test_k10_defaults_are_exactly_what_the_spec_decided(tmp_path):
    """§10 пп. 1/2/5, решения владельца 19.08. Числа — литералы, не ссылки на
    реализацию: дефолт, «проверенный» самим собой, не проверен."""
    ka = _client(tmp_path / "a").settings.keepalive
    assert ka.enabled is KEEPALIVE_DEFAULTS_LITERAL["enabled"]
    assert ka.window == KEEPALIVE_DEFAULTS_LITERAL["window"]
    assert ka.min_dialogs_per_month == KEEPALIVE_DEFAULTS_LITERAL["min_dialogs_per_month"]


def test_k10_all_three_fields_are_actually_read_from_yaml(tmp_path):
    """Ключ в списке известных, но не читаемый, — та же тихая ложь, что и
    `classifier_model` 19.08: настройка выглядит применённой и не применена."""
    cfg = _client(tmp_path / "a",
                  extra=("\nkeepalive:\n  enabled: true\n"
                         '  window: "07:30-19:45"\n  min_dialogs_per_month: 111\n'))
    ka = cfg.settings.keepalive
    assert ka.enabled is True
    assert ka.window == "07:30-19:45"
    assert ka.min_dialogs_per_month == 111


def test_k10_unknown_key_inside_the_block_is_refused_by_name(tmp_path):
    with pytest.raises(ConfigError) as e:
        _client(tmp_path / "a",
                extra="\nkeepalive:\n  enabled: true\n  min_dialogs_per_moth: 25\n")
    msg = str(e.value)
    assert "min_dialogs_per_moth" in msg, f"опечатка не названа: {msg}"
    assert "keepalive" in msg


@pytest.mark.parametrize("slug", sorted(p.name for p in CLIENTS.iterdir()
                                        if (p / "settings.yaml").exists()))
def test_k10_every_production_client_still_loads_with_keepalive_defaults(slug):
    """ЧТЕНИЕ боевых конфигов. Клиент, переставший подниматься из-за новых
    ключей, — авария, а не строгость (гардиан уходит в шторм рестартов)."""
    cfg = load_config(CLIENTS, slug)
    ka = cfg.settings.keepalive
    assert isinstance(ka.enabled, bool)
    assert isinstance(ka.window, str) and "-" in ka.window
    assert isinstance(ka.min_dialogs_per_month, int)


def test_k10_no_production_settings_file_has_an_unknown_keepalive_key():
    """Прямым сравнением — чтобы падение называло КЛЮЧ и ФАЙЛ, а не только
    «клиент не грузится»."""
    known = set(loader_mod._KEEPALIVE_KEYS)
    checked = 0
    for path in sorted(CLIENTS.glob("*/settings.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        block = raw.get("keepalive")
        checked += 1
        if block is None:
            continue
        assert isinstance(block, dict), f"{path.parent.name}: keepalive не mapping"
        unknown = sorted(set(block) - known)
        assert not unknown, f"{path.parent.name}: неизвестные ключи keepalive {unknown}"
    assert checked >= 2, ("боевых конфигов не найдено — сторож прошёл вхолостую; "
                          f"искали в {CLIENTS}")


# ═══════════ А. АЛЕРТ ВЛАДЕЛЬЦУ ОБЯЗАН ДОХОДИТЬ, А НЕ ЛОГИРОВАТЬСЯ ═══════════
#
# §5, красный подраздел «Алерт владельцу обязан ДОХОДИТЬ, а не логироваться».
# Дословно: «`log.warning` здесь — это ровно тот отказ, из-за которого 23.07
# смерть кэша прожила сутки незамеченной. Логи никто не читает; арка, которая
# молчит в лог о собственной поломке, слепа так же, как была.»
#
# Доставка — «тем же путём, что `_maybe_degraded_alert` (`chatter/run.py:746`):
# `deps.notifier` + карточка в `console_cards` + дебаунс через `runtime_flag`,
# и ГРОМКАЯ запись, если notifier вернул `None` (посчитано ≠ доехало)».
#
# Четыре способа этому алерту стать зелёной ширмой, все сторожатся поимённо:
#
#   1. Он НЕ ДОХОДИТ — есть строка в логе и больше ничего (А1, А2).
#   2. Он «доставлен» НА БУМАГЕ — notifier вернул None, а код записал успех:
#      дебаунс сожжён, следующий промах молчит сутки (А3, А4). Это тот же
#      «посчитано ≠ доехало», что уже стоил суток слепоты 23.07.
#   3. Он ВСЕГДА КРАСНЫЙ — алертит на любой промах, включая законный истёкший
#      TTL и сетевую икоту. Сигнал, красный при нормальной работе, — фон, а не
#      сторож (А0, А5, А6).
#   4. Он ШТОРМИТ — сломанный префикс промахивается КАЖДЫЕ 50 минут, и без
#      дебаунса владелец получит ~28 одинаковых карточек в сутки, после чего
#      выключит уведомления и ослепнет добровольно (А7, А8).

# Породы промаха разводятся РАЗРЫВОМ с последнего касания префикса.
# TTL записи = 1 час; период пинга = 50 мин (§3).
#   * 55 мин — пинговать ПОРА (> 50) и запись обязана быть ЖИВА (< TTL).
#     Промах здесь = префикс сломан. Это и есть повод для алерта.
#   * 2 часа — TTL истёк ЗАКОННО. Промах здесь уликой не является.
CACHE_TTL_SEC_LITERAL = 3600.0
GAP_RECORD_ALIVE = 55 * 60.0
GAP_RECORD_DEAD = 2 * 3600.0

# «Записано ГРОМКО» — запись уровня WARNING и выше, называющая доставку.
# Список литеральный: сторож на «хоть что-нибудь в логе» пропустил бы
# INFO-строку, которую никто не увидит, то есть ровно проверяемый дефект.
DELIVERY_LOG_MARKERS = ("notifier", "достав", "deliver", "не дошёл", "не доехал",
                        "не доставлен")


def test_a_ttl_literal_agrees_with_the_detector():
    """Разрыв, по которому здесь разводятся породы, — тот же TTL, которым живёт
    `cache_health`. Разойдутся — сторож начнёт судить промахи по СВОЕМУ числу
    (два числа на одну вещь: меньшее гасит большее молча)."""
    assert CACHE_TTL_SEC == CACHE_TTL_SEC_LITERAL
    assert GAP_RECORD_ALIVE < CACHE_TTL_SEC_LITERAL < GAP_RECORD_DEAD
    assert GAP_RECORD_ALIVE > PING_PERIOD_SEC_LITERAL


class SpyNotifier(Notifier):
    """Двойник владельца. Наследуется от боевого `Notifier`: вырастет
    интерфейс — сторож упадёт громко, а не разойдётся с продом молча.

    `delivered=False` моделирует то, ради чего написан §5: notify отработал,
    вернул `None`, и владелец НЕ ПОЛУЧИЛ ничего.
    """

    def __init__(self, *, delivered: bool = True, first_msg_id: int = 4200):
        self.cards: list[Card] = []
        self.handles: list[CardHandle | None] = []
        self.delivered = delivered
        self._next = first_msg_id

    def notify(self, card: Card) -> CardHandle | None:
        self.cards.append(card)
        if not self.delivered:
            self.handles.append(None)
            return None
        handle = CardHandle(ref=f"me:{self._next}")
        self._next += 1
        self.handles.append(handle)
        return handle

    def edit(self, handle: CardHandle, text_html: str) -> None:
        raise AssertionError("алерт keep-alive не правит карточки — edit звать незачем")

    def update_card(self, handle: CardHandle, card: Card) -> bool:
        raise AssertionError("алерт keep-alive не обновляет карточки")

    @property
    def has_buttons(self) -> bool:
        return False

    def msg_ids(self) -> list[int]:
        """`msg_id` доставленных карточек — тем же разбором, что и боевой
        `_post_escalation_card`: int(handle.ref.split(":")[-1])."""
        return [int(h.ref.split(":")[-1]) for h in self.handles if h is not None]


def _seed_touch(store: Store, tag: str, *, now: float, ago: float,
                cache_read: int = 8000) -> None:
    """Одно касание префикса `tag` (боевой вызов) `ago` секунд назад."""
    store.add_llm_usage(tag=tag, model="claude-sonnet-5", input_tokens=100,
                        output_tokens=10, cache_read_input_tokens=cache_read,
                        cache_creation_input_tokens=0, ts=now - ago)


def _broken_prefix_store(tmp_path: Path, *, now: float, name: str = "s") -> Store:
    """Сцена «сломан ОДИН префикс».

    `brain` молчал 55 минут — пинговать пора, а запись обязана быть жива.
    `classifier` тронут минуту назад — он НЕ пора, пинга по нему не будет.
    Так на проход приходится РОВНО ОДИН пинг и ровно один промах: иначе
    «сколько пришло алертов» смешалось бы с «на сколько префиксов их шлют».
    """
    store = _store(tmp_path / name)
    _seed_touch(store, "brain", now=now, ago=GAP_RECORD_ALIVE)
    _seed_touch(store, "classifier", now=now, ago=60.0)
    return store


def _miss_llm() -> SpyLLM:
    """Пинг уходит и ВОЗВРАЩАЕТ ПРОМАХ: cache_read == 0 (§5, определение)."""
    return SpyLLM(cache_read=0)


# ── А0. Сигнал, красный при нормальной работе, — фон, а не сторож ────────────

def test_a0_successful_ping_raises_no_alert(tmp_path, flags):
    """Пинг попал в живую запись — владельцу сообщать нечего."""
    now = _at(12)
    cfg = _client(tmp_path / "c", extra=KEEPALIVE_ON)
    store = _broken_prefix_store(tmp_path, now=now)
    spy, owner = SpyLLM(cache_read=8000), SpyNotifier()
    results = _cycle(cfg, store, spy, now=now, notifier=owner)
    assert any(r.sent for r in results), "сцена вырождена: пинг не ушёл вовсе"
    assert owner.cards == [], (
        "алерт на УСПЕШНОМ пинге — такой сигнал красный всегда, и владелец "
        "перестанет его читать первым же днём")


# ── А1/А2. Алерт ДОХОДИТ: notifier + карточка в console_cards ───────────────

def test_a1_broken_prefix_alert_reaches_the_owner(tmp_path, flags):
    """Породу «префикс сломан» §5 велит НЕ логировать, а ДОСТАВЛЯТЬ."""
    now = _at(12)
    cfg = _client(tmp_path / "c", extra=KEEPALIVE_ON)
    store = _broken_prefix_store(tmp_path, now=now)
    owner = SpyNotifier()
    results = _cycle(cfg, store, _miss_llm(), now=now, notifier=owner)

    assert any(r.sent for r in results), "сцена вырождена: пинг не ушёл вовсе"
    assert [r.cache_read for r in results if r.sent] == [0], (
        "сцена вырождена: пинг не промахнулся, алертить не о чем")
    assert len(owner.cards) == 1, (
        f"владелец получил {len(owner.cards)} карточек вместо одной — сломанный "
        f"префикс это регрессия арки кэша 23.07, та самая, что прожила сутки "
        f"незамеченной ИМЕННО потому, что ушла в лог")


def test_a2_delivered_alert_is_recorded_in_console_cards(tmp_path, flags):
    """Карточка без строки в `console_cards` — карточка, которую пульт не
    узнает: `card_contact` вернёт None, и обратная связь по ней потеряна."""
    now = _at(12)
    cfg = _client(tmp_path / "c", extra=KEEPALIVE_ON)
    store = _broken_prefix_store(tmp_path, now=now)
    owner = SpyNotifier()
    _cycle(cfg, store, _miss_llm(), now=now, notifier=owner)

    assert owner.msg_ids(), "доставки не было — проверять запись карточки нечего"
    for msg_id in owner.msg_ids():
        assert store.card_contact(msg_id) is not None, (
            f"карточка msg_id={msg_id} доставлена, но в console_cards её нет — "
            f"«посчитано ≠ доехало» логируется ОБОИМИ концами")


# ── А3/А4. notifier вернул None: громко и БЕЗ сожжённого дебаунса ────────────

def test_a3_undelivered_alert_is_logged_loudly(tmp_path, flags, caplog):
    """`None` от notifier — это НЕ доставка. Молчание здесь = владелец глух, и
    об этом никто не знает."""
    now = _at(12)
    cfg = _client(tmp_path / "c", extra=KEEPALIVE_ON)
    store = _broken_prefix_store(tmp_path, now=now)
    owner = SpyNotifier(delivered=False)

    with caplog.at_level(logging.WARNING):
        _cycle(cfg, store, _miss_llm(), now=now, notifier=owner)

    assert len(owner.cards) == 1, "доставку даже не попытались"
    loud = [r for r in caplog.records if r.levelno >= logging.WARNING
            and any(m in r.getMessage().lower() for m in DELIVERY_LOG_MARKERS)]
    assert loud, (
        "notifier вернул None, и об этом НИ СЛОВА уровня WARNING+ — недоставка "
        f"проглочена (DEV-18). Что было в логе: "
        f"{[(r.levelname, r.getMessage()) for r in caplog.records]}")


def test_a3_undelivered_alert_writes_no_card_row(tmp_path, flags):
    """Строка в `console_cards` о карточке, которой нет, — вторая ложь поверх
    первой: пульт будет считать владельца оповещённым."""
    now = _at(12)
    cfg = _client(tmp_path / "c", extra=KEEPALIVE_ON)
    store = _broken_prefix_store(tmp_path, now=now)
    owner = SpyNotifier(delivered=False)
    _cycle(cfg, store, _miss_llm(), now=now, notifier=owner)
    assert owner.msg_ids() == []
    for probe in range(4200, 4210):
        assert store.card_contact(probe) is None, (
            f"карточка msg_id={probe} записана, хотя доставки не было")


def test_a4_failed_delivery_does_not_burn_the_debounce(tmp_path, flags):
    """AUDIT D4 дословно (`chatter/run.py`): «флаг = подтверждённый успех, а не
    намерение». Пометив НЕдоставленный алерт доставленным, мы сжигаем окно
    дебаунса — и следующий промах молчит ровно столько, сколько оно длится.
    Это ровно тот отказ, который §5 называет причиной суток слепоты 23.07.
    """
    now = _at(12)
    cfg = _client(tmp_path / "c", extra=KEEPALIVE_ON)
    store = _broken_prefix_store(tmp_path, now=now)

    dead = SpyNotifier(delivered=False)
    _cycle(cfg, store, _miss_llm(), now=now, notifier=dead)
    assert len(dead.cards) == 1

    later = now + PING_PERIOD_SEC_LITERAL + 300.0      # следующий промах, ~55 мин
    _seed_touch(store, "classifier", now=later, ago=60.0)
    alive = SpyNotifier()
    _cycle(cfg, store, _miss_llm(), now=later, notifier=alive)
    assert len(alive.cards) == 1, (
        "после НЕдоставленного алерта следующий промах промолчал — дебаунс "
        "сожжён намерением, а не фактом доставки")


# ── А5/А6. Три породы промаха различаются ───────────────────────────────────

def test_a5_expired_ttl_miss_raises_no_alert(tmp_path, flags):
    """§5, строка «пинг опоздал (TTL истёк)»: действие — чинить период и
    планировщик, а НЕ алерт. Запись умерла законно, префикс цел."""
    now = _at(12)
    cfg = _client(tmp_path / "c", extra=KEEPALIVE_ON)
    store = _store(tmp_path / "dead")
    _seed_touch(store, "brain", now=now, ago=GAP_RECORD_DEAD)
    _seed_touch(store, "classifier", now=now, ago=60.0)

    owner = SpyNotifier()
    results = _cycle(cfg, store, _miss_llm(), now=now, notifier=owner)
    assert any(r.sent for r in results), "сцена вырождена: пинг не ушёл вовсе"
    assert owner.cards == [], (
        "алерт на ЗАКОННО истёкшем TTL — владельца зовут чинить целый префикс")


def test_a6_transport_failure_raises_no_alert(tmp_path, flags):
    """§5, строка «пинг не ушёл (сеть/5xx)»: ретрай и пропуск цикла, не алерт.
    Сетевая икота фоновой задачи — не поломка префикса."""
    now = _at(12)
    cfg = _client(tmp_path / "c", extra=KEEPALIVE_ON)
    store = _broken_prefix_store(tmp_path, now=now)
    owner = SpyNotifier()
    results = _cycle(cfg, store, SpyLLM(fail_all=True), now=now, notifier=owner)

    assert results and not any(r.sent for r in results)
    assert all(r.error for r in results if not r.sent)
    assert owner.cards == [], (
        "алерт на транспортном сбое: 5xx у Anthropic поднимет владельца ночью "
        "и научит его игнорировать этот сигнал")


# ── А7/А8. ДЕБАУНС: один алерт на поломку, а не 28 карточек в сутки ──────────

def test_a7_repeated_misses_do_not_storm_the_owner(tmp_path, flags):
    """Сломанный префикс промахивается КАЖДЫЕ 50 минут. Без дебаунса это ~28
    одинаковых карточек в сутки — и владелец выключает уведомления."""
    now = _at(9)
    cfg = _client(tmp_path / "c", extra=KEEPALIVE_ON)
    store = _broken_prefix_store(tmp_path, now=now)
    owner = SpyNotifier()

    _cycle(cfg, store, _miss_llm(), now=now, notifier=owner)
    assert len(owner.cards) == 1, "первый промах обязан ДОЙТИ"

    tick = now
    for _ in range(5):
        tick += PING_PERIOD_SEC_LITERAL + 300.0
        _seed_touch(store, "classifier", now=tick, ago=60.0)
        _cycle(cfg, store, _miss_llm(), now=tick, notifier=owner)
    assert len(owner.cards) == 1, (
        f"владелец получил {len(owner.cards)} карточек за 6 промахов подряд — "
        f"дебаунса нет, сигнал превращается в шум")


def test_a8_debounce_expires_and_the_alert_returns(tmp_path, flags):
    """Обратная половина А7. Дебаунс, который не истекает, — это «сказали один
    раз и замолчали навсегда»: поломка, о которой напомнить некому.

    Окно закреплено решением 20.08: `control.status_window_hours` — то же,
    что у `_maybe_degraded_alert`. Число берётся из ДЕФОЛТА боевого
    `ControlConfig`, а не переписывается сюда литералом: разъехавшись, они
    дали бы сторожа, который «проверил» чужое окно.
    """
    now = _at(9)
    cfg = _client(tmp_path / "c", extra=KEEPALIVE_ON)
    store = _broken_prefix_store(tmp_path, now=now)
    owner = SpyNotifier()
    _cycle(cfg, store, _miss_llm(), now=now, notifier=owner)
    assert len(owner.cards) == 1

    window = ControlConfig().status_window_hours * 3600.0
    assert window == 24 * 3600.0, "дефолт окна алертов изменился — сверить §5"
    later = now + window + 3600.0
    _seed_touch(store, "brain", now=later, ago=GAP_RECORD_ALIVE)
    _seed_touch(store, "classifier", now=later, ago=60.0)
    _cycle(cfg, store, _miss_llm(), now=later, notifier=owner)
    assert len(owner.cards) == 2, (
        "по истечении окна дебаунса поломка всё ещё молчит — это не дебаунс, "
        "а глушилка")


def test_a10_alert_for_one_client_does_not_silence_another(tmp_path, flags):
    """Store в раннере ОДИН на процесс (`runner.primary_store()`), а клиентов в
    нём несколько. Дебаунс под ОБЩИМ ключом `runtime_flags` заглушит поломку
    второго клиента чужим алертом — §6 п.4/п.5: «отказ пингов одного клиента не
    должен глушить остальных», «замер обязан быть пер-клиентным».

    ВЫВЕДЕНО из §6, а не сказано в §5 дословно, — при разборе учитывать.
    """
    now = _at(12)
    a = _client(tmp_path / "a", slug="alpha", extra=KEEPALIVE_ON, persona="Аня А.")
    b = _client(tmp_path / "b", slug="beta", extra=KEEPALIVE_ON, persona="Оля Б.")
    store = _broken_prefix_store(tmp_path, now=now, name="shared")
    owner = SpyNotifier()

    _cycle(a, store, _miss_llm(), now=now, notifier=owner)
    assert len(owner.cards) == 1, "первый клиент не достучался"

    # Второй клиент, тот же момент и та же общая база: префикс у него СВОЙ,
    # и его поломка — отдельное событие.
    _seed_touch(store, "brain", now=now, ago=GAP_RECORD_ALIVE)
    _seed_touch(store, "classifier", now=now, ago=60.0)
    _cycle(b, store, _miss_llm(), now=now, notifier=owner)
    assert len(owner.cards) == 2, (
        "поломка второго клиента заглушена алертом первого — дебаунс общий на "
        "процесс, а кэш-записи у клиентов разные (§6: «общего кэша нет и быть "
        "не может»)")


# ── А9. У алерта СВОЙ счётчик: порог классификатора он не трогает ────────────

def test_a9_broken_prefix_alert_is_not_a_classifier_failure(tmp_path, flags):
    """§5 п.2 в силе и для породы «префикс сломан»: считаем ТЕМ ЖЕ счётчиком,
    что и боевой алерт деградации (`chatter/run.py`), а не своей арифметикой."""
    now = _at(12)
    cfg = _client(tmp_path / "c", extra=KEEPALIVE_ON)
    store = _broken_prefix_store(tmp_path, now=now)
    owner = SpyNotifier()
    _cycle(cfg, store, _miss_llm(), now=now, notifier=owner)

    assert len(owner.cards) == 1, "сцена вырождена: алерта не было"
    assert store.count_events("classifier_error", since_ts=0) == 0
    assert store.count_events("classifier_recovered", since_ts=0) == 0
    assert classifier_failure_count(store, now=now, window_seconds=86400.0) == 0, (
        "промах ФОНОВОГО пинга попал в счётчик сбоев классификатора — «Счётчик "
        "у алерта СВОЙ» (§5)")
    assert classifier_degraded(store, now=now, window_seconds=86400.0,
                               threshold=2) is False


def test_a9_alert_does_not_write_combat_usage_rows(tmp_path, flags):
    """Промах пишется в `llm_usage` под keepalive-тегом, и на этом всё: алерт не
    имеет права дописать боевую строку, иначе он испортит замер §4, который сам
    же и охраняет."""
    now = _at(12)
    cfg = _client(tmp_path / "c", extra=KEEPALIVE_ON)
    store = _broken_prefix_store(tmp_path, now=now)
    before = _usage_calls(store)
    spy, owner = _miss_llm(), SpyNotifier()
    _cycle(cfg, store, spy, now=now, notifier=owner)

    after = _usage_calls(store)
    for combat in sorted(COMBAT_TAGS_LITERAL):
        assert after.get(combat, 0) == before.get(combat, 0), (
            f"алерт дописал строку под боевым тегом {combat!r}")
    new = {t for t, n in after.items() if n > before.get(t, 0)}
    assert new <= KEEPALIVE_TAGS_LITERAL, f"неожиданные новые теги: {sorted(new)}"


# ═════ Б. ДВЕ ПЕРСОНЫ В ОДНОМ ПРОЦЕССЕ — ОТКАЗ ГРОМКИЙ, а не «как получится» ═
#
# §6.1 (найдено 20.08). `llm_usage` НЕ содержит колонки клиента, а
# `TelethonRunner.primary_store()` отдаёт ОДИН Store всем персонам процесса —
# дословно из его докстринга: «это не store первичной персоны — это store,
# разделяемый всеми». Отсюда свежесть тега `brain` становится ОБЩЕЙ: боевой
# вызов персоны B подавляет пинг персоны A как «трафик и так частый» (§3), и
# запись A остывает молча — ровно у того клиента, ради которого арку включали.
#
# Решение владельца до появления колонки: «keep-alive ОТКАЗЫВАЕТСЯ громко. Если
# процесс держит больше одной персоны и хоть у одной `keepalive.enabled: true`
# — пингов нет ни у кого, в лог идёт `ERROR` с обеими причинами».
#
# Три способа этому отказу стать зелёной ширмой, все сторожатся поимённо:
#
#   1. Отказ ТИХИЙ — пингов нет, и в логе ничего. Снаружи неотличимо от
#      «режим просто выключен», и через месяц вопрос «почему счёт не упал»
#      не с чем связать (Б2).
#   2. Отказ ЧАСТИЧНЫЙ — включённая персона не пингует, а вторая пингует
#      «раз уж она тут». Свежесть общая, значит греется она мимо (Б1, Б3).
#   3. Отказ ВСЕГДА — одна персона тоже перестала пинговать, и мы поймали не
#      защиту, а поломку арки (Б4). Плюс отказ на выключенных тумблерах —
#      ERROR каждый тик в проде, где две персоны и режим выключен (Б5).
#
# Проверяется УРОВЕНЬ ПРОЦЕССА (`telethon_run.keepalive_tick`), а не
# `run_keepalive_cycle`: последний видит РОВНО одного клиента и про соседей по
# процессу знать не может — там этот отказ физически негде принять.

# Две причины отказа, обе обязаны прозвучать. Списки ЛИТЕРАЛЬНЫЕ: сторож на
# «хоть какой-нибудь ERROR» пропустил бы запись «keep-alive выключен», из
# которой владелец не поймёт ни что сломано, ни почему это не чинится сейчас.
CAUSE_NO_CLIENT_COLUMN = ("колонк", "llm_usage", "column")
CAUSE_SHARED_STORE = ("store", "персон", "процесс", "общ")


class SpyAnthropicLLM(SpyLLM, AnthropicLLM):
    """Шпион, который ПРОХОДИТ `isinstance(..., AnthropicLLM)`.

    `keepalive_tick` греет только персон на РЕАЛЬНОМ клиенте (`load_personas`
    ставит `FakeLLM` там, где нет ключа, и греть там нечего). Без этого
    наследования сторож «две персоны не пингуют» был бы зелёным по построению:
    список живых персон оказался бы пуст, и отказ проверять было бы нечего.

    `AnthropicLLM.__init__` НЕ вызывается СПЕЦИАЛЬНО: он импортирует SDK и
    создаёт живого `anthropic.Anthropic()` — то есть тест завёл бы настоящий
    клиент с настоящим ключом. Ни одна сеть отсюда не поднимается: весь
    наблюдаемый путь (`send_raw`, `complete`) переопределён `SpyLLM`.
    """

    def __init__(self, **kwargs):
        SpyLLM.__init__(self, **kwargs)


def _bundle(cfg, llm):
    """`PersonaBundle` из боевых классов, а не самодельная заглушка: вырастет
    интерфейс персоны — сторож упадёт громко, а не разойдётся с продом молча."""
    from chatter.core.brain import Brain
    from chatter.run import Deps

    deps = Deps(cfg=cfg, store=_BUNDLE_STORE[0], brain=Brain(llm, cfg),
                rng=random.Random(0), clock=lambda: 0.0, sleep=lambda _s: None,
                control=cfg.settings.control)
    return PersonaBundle(cfg=cfg, deps=deps, llm=llm)


# Store процесса ОДИН на всех — это и есть предмет §6.1, поэтому он передаётся
# через список-ячейку, а не пересоздаётся на каждую персону: собрав персонам
# РАЗНЫЕ store, сторож проверял бы конфигурацию, которой в проде нет.
_BUNDLE_STORE: list = [None]


class FakeRunner:
    """Раннер процесса: словарь персон + ОДИН общий Store (§6.1)."""

    def __init__(self, personas: dict, store):
        self.personas = personas
        self._store = store

    def primary_store(self):
        return self._store


def _runner(tmp_path: Path, *, slugs_enabled: dict[str, bool], now: float):
    """Процесс с N персонами. `slugs_enabled` — тумблер keep-alive у каждой.

    Трафик засеян СТАРЫМ (2 часа) по обоим тегам: без этого «пингов нет» было
    бы правдой и без всякого отказа, и сторож ловил бы тишину вместо защиты.
    """
    store = _store(tmp_path / "process")
    _seed_touch(store, "brain", now=now, ago=GAP_RECORD_DEAD)
    _seed_touch(store, "classifier", now=now, ago=GAP_RECORD_DEAD)
    _BUNDLE_STORE[0] = store

    personas, spies = {}, {}
    for slug, on in slugs_enabled.items():
        block = KEEPALIVE_ON if on else (
            "\nkeepalive:\n  enabled: false\n"
            '  window: "00:00-23:59"\n  min_dialogs_per_month: 0\n')
        cfg = _client(tmp_path / f"cfg-{slug}", slug=slug, extra=block,
                      persona=f"Персона {slug}.")
        llm = SpyAnthropicLLM()
        spies[slug] = llm
        personas[slug] = _bundle(cfg, llm)
    return FakeRunner(personas, store), spies, store


def _pinged(spies: dict) -> dict:
    return {slug: len(s.pings) + len(s.calls) + len(s.sdk_calls)
            for slug, s in spies.items()}


def _errors(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]


def _tick(runner, *, now: float):
    return asyncio.run(keepalive_tick(runner, now=now))


# ── Б1. Ноль пингов у ОБЕИХ персон ──────────────────────────────────────────

def test_b1_two_personas_ping_nobody(tmp_path, flags, caplog):
    """«Пингов нет ни у кого» — дословно §6.1. Не «у включённой нет, у
    выключенной и так не было»: свежесть общая, поэтому греть нельзя НИКОГО."""
    now = _at(12)
    runner, spies, store = _runner(
        tmp_path, slugs_enabled={"alpha": True, "beta": True}, now=now)
    before = _usage_calls(store)

    with caplog.at_level(logging.ERROR):
        out = _tick(runner, now=now)

    assert _pinged(spies) == {"alpha": 0, "beta": 0}, (
        f"процесс с двумя персонами всё-таки пинговал: {_pinged(spies)} — "
        f"свежесть тега общая, значит греется чужая запись, а своя остывает")
    assert all(not r.sent for res in out.values() for r in res), (
        f"tick отчитался об отправленных пингах: {out}")
    assert _usage_calls(store) == before, (
        "отказавший keep-alive дописал строку в денежный леджер llm_usage")


def test_b1_refusal_holds_when_only_one_toggle_is_on(tmp_path, flags, caplog):
    """§6.1: «и хоть у одной `keepalive.enabled: true`». Вторая персона с
    выключенным тумблером не делает конфигурацию безопасной — строки в
    `llm_usage` она пишет БОЕВЫМИ вызовами, и именно они подавляют пинг."""
    now = _at(12)
    runner, spies, _ = _runner(
        tmp_path, slugs_enabled={"alpha": True, "beta": False}, now=now)

    with caplog.at_level(logging.ERROR):
        _tick(runner, now=now)

    assert _pinged(spies) == {"alpha": 0, "beta": 0}
    assert _errors(caplog), "отказ прошёл молча"


# ── Б2. Отказ ГРОМКИЙ и называет ОБЕ причины ────────────────────────────────

def test_b2_refusal_is_logged_at_error_with_both_causes(tmp_path, flags, caplog):
    """Тихий отказ здесь неотличим от «режим просто выключен», а виден он был
    бы только по СЧЁТУ за холодные ходы — через месяц и без единой зацепки."""
    now = _at(12)
    runner, _, _ = _runner(
        tmp_path, slugs_enabled={"alpha": True, "beta": True}, now=now)

    with caplog.at_level(logging.ERROR):
        _tick(runner, now=now)

    errs = _errors(caplog)
    assert errs, (
        "процесс отказался пинговать МОЛЧА — ровно тот класс, из-за которого "
        f"23.07 смерть кэша прожила сутки. Весь лог: "
        f"{[(r.levelname, r.getMessage()) for r in caplog.records]}")
    blob = " ".join(errs).lower()
    assert any(m in blob for m in CAUSE_NO_CLIENT_COLUMN), (
        f"в ERROR не названа причина «нет колонки клиента в llm_usage»: {errs}")
    assert any(m in blob for m in CAUSE_SHARED_STORE), (
        f"в ERROR не названа причина «общий store на все персоны»: {errs}")


# ── Б3/Б4/Б5. Обратные половины: отказ не должен стать поломкой ─────────────

def test_b4_single_persona_still_pings(tmp_path, flags, caplog):
    """Один клиент — один процесс: это ПРОД сегодня (`--personas volska`,
    `--personas yarina`). Отказ, задевший его, — не защита, а остановка арки."""
    now = _at(12)
    runner, spies, store = _runner(
        tmp_path, slugs_enabled={"alpha": True}, now=now)

    with caplog.at_level(logging.ERROR):
        out = _tick(runner, now=now)

    assert _pinged(spies)["alpha"] > 0, (
        "единственная персона перестала пинговать — сторож поймал не отказ "
        "§6.1, а поломку keep-alive")
    assert any(r.sent for res in out.values() for r in res)
    assert set(_usage_calls(store)) - set(COMBAT_TAGS_LITERAL) <= KEEPALIVE_TAGS_LITERAL
    assert _errors(caplog) == [], (
        f"одна персона, а в логе ERROR: {_errors(caplog)}")


def test_b5_two_personas_with_toggles_off_do_not_shout(tmp_path, flags, caplog):
    """Отказ срабатывает на условие «есть включённый тумблер», а не на «персон
    больше одной». Иначе прод с двумя выключенными персонами получал бы ERROR
    каждый тик — сигнал, красный при нормальной работе, это фон."""
    now = _at(12)
    runner, spies, _ = _runner(
        tmp_path, slugs_enabled={"alpha": False, "beta": False}, now=now)

    with caplog.at_level(logging.ERROR):
        _tick(runner, now=now)

    assert _pinged(spies) == {"alpha": 0, "beta": 0}
    assert _errors(caplog) == [], (
        f"ERROR на выключенных тумблерах — фоновый шум каждый тик: "
        f"{_errors(caplog)}")


# ═══ В. ПОРОГ ВКЛЮЧЕНИЯ ПО ОБЪЁМУ — денежная защита, а не удобство ══════════
#
# §3.1 п.4–5, §6 п.1, §9 п.3, §10 п.2. Стоимость пингов ФИКСИРОВАНА на клиента,
# а экономия растёт с объёмом: ниже порога арка уводит клиента В МИНУС. Сумма
# по портфелю этого не покажет — §6 п.1 дословно: «портфель из 10 тихих
# клиентов… окупятся только при 132 диалогах суммарно. Режим обязан включаться
# ПЕР-КЛИЕНТ по его объёму, а не глобальным флагом».
#
# Ловушка этой ветки в том, что её отказ ВЫГЛЯДИТ как исправная работа: пингов
# нет, ошибок нет, тумблер включён. Увидеть его можно только по счёту за месяц
# — то есть тогда, когда деньги уже потрачены, и ровно у того клиента, ради
# которого порог и придуман. Поэтому каждый сторож «ниже порога → тишина» здесь
# идёт ПАРОЙ со своим близнецом «выше порога → пинги», отличающимся РОВНО одной
# переменной. Одиночный сторож на тишину зелен по построению: тишину даёт и
# сломанный keep-alive.
#
# ── ГРАНИЦА, зафиксированная здесь явно ────────────────────────────────────
# Спека говорит «ниже порога — ноль пингов» (§3.1 п.4) и не оговаривает
# равенство. Читаем СТРОГО: `диалогов >= порога` → работаем, `<` → тишина.
# Обоснование — тот же выбор, что уже сделан в `classifier_degraded`: «сбоев за
# окно НАБРАЛОСЬ НА ПОРОГ» сравнивается через `>=`, потому что со строгим `>`
# порог 5 требовал шестого сбоя и не срабатывал никогда. Порог, который
# требует «порог плюс один», — это молча другой порог.

VOLUME_WINDOW_DAYS_LITERAL = 30
VOLUME_WINDOW_SEC_LITERAL = VOLUME_WINDOW_DAYS_LITERAL * 24 * 3600.0
INSIDE_WINDOW_AGO = 3 * 24 * 3600.0        # 3 суток назад — заведомо внутри
OUTSIDE_WINDOW_AGO = 40 * 24 * 3600.0      # 40 суток назад — заведомо снаружи


def _ka_yaml(*, enabled: bool = True, window: str = "00:00-23:59",
             min_dialogs: int | None = None) -> str:
    """Блок `keepalive` для settings.yaml. `min_dialogs=None` — поля НЕТ вовсе,
    то есть в силе дефолт загрузчика (25)."""
    out = ["", "keepalive:",
           f"  enabled: {'true' if enabled else 'false'}",
           f'  window: "{window}"']
    if min_dialogs is not None:
        out.append(f"  min_dialogs_per_month: {min_dialogs}")
    return "\n".join(out) + "\n"


def _volume_store(tmp_path: Path, *, now: float, contacts: int,
                  msgs_per_contact: int = 1, ago: float = INSIDE_WINDOW_AGO,
                  role: str = "user", name: str = "vol",
                  store: Store | None = None) -> Store:
    """База с ЗАДАННЫМ объёмом и заведомо остывшими префиксами.

    Оба тега тронуты 2 часа назад: пинговать ПОРА (§3), окно суток открыто.
    Значит единственная причина, по которой пинга может не быть, — порог
    объёма. Иначе сторож ловил бы тишину любого происхождения.
    """
    if store is None:
        store = _store(tmp_path / name)
        _seed_touch(store, "brain", now=now, ago=GAP_RECORD_DEAD)
        _seed_touch(store, "classifier", now=now, ago=GAP_RECORD_DEAD)
    for i in range(contacts):
        for j in range(msgs_per_contact):
            store.add_message(f"lead{i}:demo", role, "добрий день", now - ago - j)
    return store


def _run_volume(cfg, store: Store, *, now: float):
    """Один проход. Возвращает (шпион, результаты, дельта строк `llm_usage`)."""
    spy = SpyLLM()
    before = _usage_calls(store)
    results = _cycle(cfg, store, spy, now=now)
    after = _usage_calls(store)
    delta = sum(after.values()) - sum(before.values())
    return spy, results, delta


def _assert_silent(spy: SpyLLM, results, delta: int, why: str) -> None:
    assert spy.pings == [] and spy.calls == [] and spy.sdk_calls == [], why
    assert not any(r.sent for r in results), f"{why} (результаты: {results})"
    assert delta == 0, f"{why}: дописано {delta} строк в денежный леджер llm_usage"


def _assert_pinged(spy: SpyLLM, results, why: str) -> None:
    assert spy.pings or spy.calls or spy.sdk_calls, why
    assert any(r.sent for r in results), f"{why} (результаты: {results})"


# ── В1/В2. Ниже порога — тишина; выше — работа. Пара, отличие в ОДНОМ числе ──

def test_v1_volume_below_threshold_pings_nobody(tmp_path, flags):
    """Тумблер включён, окно открыто, префиксы остыли — и всё равно тишина.
    Единственная причина: объём ниже порога (§9 п.3 «тихим клиентам keep-alive
    не включается вовсе — да, ровно это и делает порог»)."""
    now = _at(12)
    cfg = _client(tmp_path / "c", extra=_ka_yaml(min_dialogs=10))
    store = _volume_store(tmp_path, now=now, contacts=9)
    spy, results, delta = _run_volume(cfg, store, now=now)
    _assert_silent(spy, results, delta,
                   "9 диалогов при пороге 10 — пинги ушли, клиент платит за "
                   "режим, который ему не окупается")


def test_v2_volume_above_threshold_pings_normally(tmp_path, flags):
    """Обратная половина В1: та же сцена, изменено РОВНО одно число.
    Без неё В1 зелен по построению — тишину даёт и сломанный keep-alive."""
    now = _at(12)
    cfg = _client(tmp_path / "c", extra=_ka_yaml(min_dialogs=10))
    store = _volume_store(tmp_path, now=now, contacts=11)
    spy, results, _ = _run_volume(cfg, store, now=now)
    _assert_pinged(spy, results,
                   "11 диалогов при пороге 10 — пингов нет, порог отказал не "
                   "тихому клиенту, а арке")


def test_v3_volume_exactly_at_threshold_pings(tmp_path, flags):
    """ГРАНИЦА, зафиксированная явно (см. шапку раздела): `>=`, а не `>`.
    Со строгим `>` порог 25 требовал бы 26 диалогов — это молча другой порог,
    ровно та ошибка, которую в `classifier_degraded` уже чинили."""
    now = _at(12)
    cfg = _client(tmp_path / "c", extra=_ka_yaml(min_dialogs=10))
    store = _volume_store(tmp_path, now=now, contacts=10)
    spy, results, _ = _run_volume(cfg, store, now=now)
    _assert_pinged(spy, results,
                   "объём РОВНО равен порогу, а пингов нет: порог требует "
                   "«порог плюс один» — это другое число, чем написано в конфиге")


# ── В4. Порог берётся ИЗ КОНФИГА, а не из константы ─────────────────────────

def test_v4_threshold_is_taken_from_the_client_config(tmp_path, flags):
    """Два клиента, ОДИН И ТОТ ЖЕ объём, разные пороги → разное поведение.

    Ловит константу в горячем пути: с ней оба клиента вели бы себя одинаково,
    а §10 п.2 требует прямо обратного — «ни одно число про объём не имеет права
    быть константой в коде», клиентов шесть и они разного объёма.
    """
    now = _at(12)
    strict = _client(tmp_path / "s", slug="strict", extra=_ka_yaml(min_dialogs=50))
    lax = _client(tmp_path / "l", slug="lax", extra=_ka_yaml(min_dialogs=5))
    store = _volume_store(tmp_path, now=now, contacts=10)

    spy_s, res_s, delta_s = _run_volume(strict, store, now=now)
    _assert_silent(spy_s, res_s, delta_s,
                   "10 диалогов при пороге 50 — порог клиента не прочитан")

    spy_l, res_l, _ = _run_volume(lax, store, now=now)
    _assert_pinged(spy_l, res_l,
                   "10 диалогов при пороге 5 — порог взят не из конфига, а "
                   "откуда-то ещё")


def test_v5_default_threshold_is_live_in_the_hot_path(tmp_path, flags):
    """Поля `min_dialogs_per_month` в конфиге НЕТ → в силе дефолт 25 (§10 п.2).

    Отдельно от К10: тот проверяет, что дефолт лежит в объекте настроек, а этот
    — что его КТО-ТО СПРАШИВАЕТ. Дефолт, живущий только в dataclass'е, ничего
    не защищает: он выглядит настроенным и не применён.
    """
    now = _at(12)
    assert KEEPALIVE_DEFAULTS_LITERAL["min_dialogs_per_month"] == 25
    cfg = _client(tmp_path / "c", extra=_ka_yaml(min_dialogs=None))
    assert cfg.settings.keepalive.min_dialogs_per_month == 25

    quiet = _volume_store(tmp_path, now=now, contacts=3, name="quiet")
    spy, results, delta = _run_volume(cfg, quiet, now=now)
    _assert_silent(spy, results, delta,
                   "3 диалога при дефолтном пороге 25 — дефолт не спрашивают")

    loud = _volume_store(tmp_path, now=now, contacts=25, name="loud")
    spy, results, _ = _run_volume(cfg, loud, now=now)
    _assert_pinged(spy, results, "25 диалогов при дефолтном пороге 25 — тишина")


# ── В6. Окно счёта — СКОЛЬЗЯЩИЕ 30 суток ────────────────────────────────────

def test_v6_contacts_older_than_thirty_days_do_not_count(tmp_path, flags):
    """§3.1 п.5: «сколько РАЗНЫХ контактов писали нам ЗА ПОСЛЕДНИЕ 30 СУТОК».

    Без окна объём считался бы «за всё время» и рос бы навсегда: клиент,
    затихший полгода назад, навечно оставался бы «крупным» и вечно платил за
    пинги. Это ровно та ошибка, что уже описана в §4.1 про «сколько
    накопилось» вместо одинакового окна дней.
    """
    now = _at(12)
    cfg = _client(tmp_path / "c", extra=_ka_yaml(min_dialogs=10))
    store = _volume_store(tmp_path, now=now, contacts=30,
                          ago=OUTSIDE_WINDOW_AGO, name="stale")
    spy, results, delta = _run_volume(cfg, store, now=now)
    _assert_silent(spy, results, delta,
                   f"30 контактов, но все писали {OUTSIDE_WINDOW_AGO / 86400:.0f} "
                   f"суток назад — объём считается не за скользящие "
                   f"{VOLUME_WINDOW_DAYS_LITERAL} суток")


def test_v6_the_same_contacts_inside_the_window_do_count(tmp_path, flags):
    """Близнец В6: тот же состав контактов, сдвинуты ВНУТРЬ окна."""
    now = _at(12)
    cfg = _client(tmp_path / "c", extra=_ka_yaml(min_dialogs=10))
    store = _volume_store(tmp_path, now=now, contacts=30,
                          ago=INSIDE_WINDOW_AGO, name="fresh")
    spy, results, _ = _run_volume(cfg, store, now=now)
    _assert_pinged(spy, results,
                   "30 свежих контактов при пороге 10 — пингов нет, окно счёта "
                   "режет живой трафик")


# ── В7. Единица — КОНТАКТ, а не СООБЩЕНИЕ ───────────────────────────────────

def test_v7_one_contact_with_many_messages_is_one_dialog(tmp_path, flags):
    """§3.1 п.5: единица — контакт. Считая сообщения, мы получили бы объём,
    завышенный ровно у тихого клиента с одним разговорчивым лидом, — и включили
    бы платный режим тому, кому он не окупается.

    Цена арки зависит от того, сколько РАЗ в месяц запись успевает остыть МЕЖДУ
    разговорами, а не от длины разговоров: двадцать сообщений одного лида — это
    одна пауза, а не двадцать.
    """
    now = _at(12)
    cfg = _client(tmp_path / "c", extra=_ka_yaml(min_dialogs=5))
    store = _volume_store(tmp_path, now=now, contacts=1, msgs_per_contact=20)
    spy, results, delta = _run_volume(cfg, store, now=now)
    _assert_silent(spy, results, delta,
                   "один контакт с 20 входящими засчитан как 20 диалогов — "
                   "объём меряется сообщениями, а не контактами")


def test_v7_five_contacts_with_one_message_each_are_five_dialogs(tmp_path, flags):
    """Близнец В7: ТО ЖЕ число входящих (20), но разложенное по пяти контактам.
    Разница в поведении доказывает, что считаются именно контакты."""
    now = _at(12)
    cfg = _client(tmp_path / "c", extra=_ka_yaml(min_dialogs=5))
    store = _volume_store(tmp_path, now=now, contacts=5, msgs_per_contact=4)
    spy, results, _ = _run_volume(cfg, store, now=now)
    _assert_pinged(spy, results,
                   "5 контактов при пороге 5 — пингов нет, контакты не считаются")


# ── В8. Считаются только ВХОДЯЩИЕ ───────────────────────────────────────────

def test_v8_outgoing_messages_do_not_inflate_the_volume(tmp_path, flags):
    """§3.1 п.5: «считаются только входящие: исходящие бывают и без лида
    (карточки, служебные ответы) и завысили бы объём ровно у тихого клиента —
    того самого, кому режим не окупается».

    Сцена буквальная: 30 контактов, которым МЫ писали, и ни одного, кто написал
    нам. Объём обязан быть нулевым.
    """
    now = _at(12)
    cfg = _client(tmp_path / "c", extra=_ka_yaml(min_dialogs=10))
    store = _volume_store(tmp_path, now=now, contacts=30, role="assistant",
                          name="outgoing")
    spy, results, delta = _run_volume(cfg, store, now=now)
    _assert_silent(spy, results, delta,
                   "30 ИСХОДЯЩИХ засчитаны как объём — карточки и служебные "
                   "ответы включили платный режим тихому клиенту")


def test_v8_the_same_contacts_writing_to_us_do_count(tmp_path, flags):
    """Близнец В8: те же 30 контактов, но роль входящая."""
    now = _at(12)
    cfg = _client(tmp_path / "c", extra=_ka_yaml(min_dialogs=10))
    store = _volume_store(tmp_path, now=now, contacts=30, role="user",
                          name="incoming")
    spy, results, _ = _run_volume(cfg, store, now=now)
    _assert_pinged(spy, results, "30 входящих контактов при пороге 10 — тишина")


def test_v8_outgoing_do_not_top_up_a_volume_below_threshold(tmp_path, flags):
    """Самый неприятный вариант того же дефекта: входящих 4 при пороге 10, но
    исходящих 26 — если считать всё подряд, объём «дотягивает» до порога, и
    режим включится ИМЕННО тихому клиенту. Разные контакты в обеих ролях,
    чтобы дефект не спрятался за `DISTINCT`."""
    now = _at(12)
    cfg = _client(tmp_path / "c", extra=_ka_yaml(min_dialogs=10))
    store = _volume_store(tmp_path, now=now, contacts=4, role="user", name="mix")
    for i in range(26):
        store.add_message(f"broadcast{i}:demo", "assistant", "картка",
                          now - INSIDE_WINDOW_AGO)
    spy, results, delta = _run_volume(cfg, store, now=now)
    _assert_silent(spy, results, delta,
                   "4 входящих + 26 исходящих дотянули до порога 10 — объём "
                   "завышен исходящими ровно там, где режим не окупается")


# ═ Г. ЧЕТВЁРТАЯ ПОРОДА: промах после ЗАКОННОЙ смены префикса (`/reload`) ═════
#
# §5, строка таблицы «префикс сменился законно (`/reload` внутри разрыва) |
# `config_changed_ts` попал в разрыв | молчать: записи ещё нет, и это НЕ
# поломка». Порода добавлена реализацией 20.08 и принята: без неё КАЖДАЯ правка
# базы знаний стреляла бы «префикс сломан», а сторож, кричащий на нормальную
# работу, перестают читать за неделю.
#
# Почему эта ветка нуждается в собственном стороже БОЛЬШЕ прочих: она ГАСИТ
# алерт. Все остальные ошибки арки дают лишний сигнал или лишний расход —
# ошибка здесь даёт ТИШИНУ на месте настоящей поломки, то есть ровно ту
# слепоту, которую сегодня уже находили дважды (мёртвая ветка алерта, §6.1).
# Гаситель обязан быть узким, и узость проверяется с ОБЕИХ сторон:
#
#   * не гасит там, где гасить не за что (Г2, Г3);
#   * не гасит НАВСЕГДА от давней правки (Г3);
#   * не гасит МОЛЧА от мусора во флаге (Г4) — иначе битый флаг становится
#     способом выключить сторожа, не оставив следа.
#
# Флаг пишет `TelethonRunner.reload_configs` (`telethon_run.py:711`):
# `store.set_runtime_flag("config_changed_ts", str(now), ts=now)` — строка с
# unix-временем в `runtime_flags`. Имя ключа литеральное, взято из БОЕВОГО
# писателя, а не из модуля keep-alive.
CONFIG_CHANGED_FLAG = "config_changed_ts"

# «Не молча» — запись WARNING+ , называющая флаг или конфиг. Список
# литеральный: сторож на «хоть что-нибудь в логе» пропустил бы INFO-строку,
# которую никто не увидит, то есть ровно проверяемый дефект.
CORRUPT_FLAG_LOG_MARKERS = ("config_changed_ts", "конфиг", "флаг", "reload")

# Мусор во флаге. Список ЛИТЕРАЛЬНЫЙ и намеренно разношёрстный: часть значений
# `float()` СЪЕДАЕТ (nan, inf), часть роняет (буквы, пустота). Обе половины
# опасны по-разному — съеденное молча становится «правдоподобным» моментом
# смены конфига, уронившее уходит в except, где его легко проглотить.
CORRUPT_FLAG_VALUES = ("", "   ", "недавно", "abc", "nan", "NaN", "inf",
                       "1e999", "-", "None", "12,5", "0x10")


def _reload_scene(tmp_path: Path, *, now: float, changed: str | None = None,
                  name: str = "rl"):
    """Сцена «сломан префикс» + при желании отметка о смене конфига.

    База та же, что у А1 (разрыв 55 мин < TTL, пинг промахивается), поэтому
    БЕЗ флага здесь обязан прилететь алерт — это и делает Г1 проверяемым, а не
    зелёным по построению.
    """
    cfg = _client(tmp_path / f"cfg-{name}", slug="demo", extra=KEEPALIVE_ON)
    store = _broken_prefix_store(tmp_path, now=now, name=name)
    if changed is not None:
        store.set_runtime_flag(CONFIG_CHANGED_FLAG, changed, ts=now)
    return cfg, store


# Момент предыдущего касания префикса: разрыв = (now - GAP_RECORD_ALIVE, now).
def _inside_gap(now: float) -> float:
    return now - GAP_RECORD_ALIVE / 2.0          # ~27 мин назад — внутри


def _before_gap(now: float) -> float:
    return now - 5 * 3600.0                      # 5 часов назад — давно снаружи


# ── Г1. Смена конфига ВНУТРИ разрыва — порода законная, алерта нет ──────────

def test_c1_config_change_inside_the_gap_silences_the_alert(tmp_path, flags):
    """После `/reload` старая запись мертва ПО ПОСТРОЕНИЮ, новой ещё нет, и
    первый пинг промахивается неизбежно. Это не поломка."""
    now = _at(12)
    cfg, store = _reload_scene(tmp_path, now=now, changed=str(_inside_gap(now)))
    owner = SpyNotifier()
    results = _cycle(cfg, store, _miss_llm(), now=now, notifier=owner)

    assert any(r.sent for r in results), "сцена вырождена: пинг не ушёл вовсе"
    assert [r.cache_read for r in results if r.sent] == [0], (
        "сцена вырождена: промаха не было, гасить нечего")
    assert owner.cards == [], (
        "алерт на ЗАКОННОЙ смене префикса — каждая правка базы знаний будет "
        "будить владельца, и через неделю он перестанет читать этот сигнал")


# ── Г2. Обратная половина: тот же разрыв БЕЗ смены конфига → алерт ЕСТЬ ─────

def test_c2_same_gap_without_config_change_still_alerts(tmp_path, flags):
    """Без этого сторожа Г1 зелен по построению: тишину даёт и сломанный алерт,
    и выключенная доставка, и промах, которого не было."""
    now = _at(12)
    cfg, store = _reload_scene(tmp_path, now=now, changed=None, name="noflag")
    owner = SpyNotifier()
    _cycle(cfg, store, _miss_llm(), now=now, notifier=owner)
    assert len(owner.cards) == 1, (
        "тот же самый разрыв без смены конфига обязан быть уликой — иначе "
        "гаситель гасит не породу, а сторожа целиком")


# ── Г3. Давняя правка не оправдывает промах ВЕЧНО ───────────────────────────

def test_c3_config_change_before_the_gap_does_not_excuse_the_miss(tmp_path, flags):
    """Флаг `config_changed_ts` живёт в `runtime_flags` и НЕ стирается: он
    хранит момент ПОСЛЕДНЕГО `/reload` навсегда. Если оправданием считается
    сам факт наличия флага, а не его попадание В РАЗРЫВ, то первая же правка
    конфига выключает алерт до конца жизни клиента — молча и необратимо.

    Разрыв здесь тот же (55 мин), а правка была 5 часов назад: к моменту
    предыдущего касания новый префикс уже жил, запись по нему обязана была
    быть создана и жива. Промах — улика.
    """
    now = _at(12)
    cfg, store = _reload_scene(tmp_path, now=now, changed=str(_before_gap(now)),
                               name="old")
    owner = SpyNotifier()
    _cycle(cfg, store, _miss_llm(), now=now, notifier=owner)
    assert len(owner.cards) == 1, (
        "давняя правка конфига погасила алерт — одна правка базы знаний "
        "выключает сторожа НАВСЕГДА, и об этом никто не узнает")


# ── Г4. Порченый флаг НЕ гасит алерт МОЛЧА ──────────────────────────────────

@pytest.mark.parametrize("bad", CORRUPT_FLAG_VALUES)
def test_c4_corrupt_flag_does_not_silence_the_alert_quietly(tmp_path, flags,
                                                            caplog, bad):
    """§5 дословно: «Порченое значение `config_changed_ts` НЕ гасит алерт молча
    — иначе битый флаг стал бы способом выключить сторожа, не оставив следа».

    Требование НЕ в том, чтобы алерт обязательно ушёл: у мусора нет верного
    прочтения, и «считать разрыв неоправданным» — законный выбор, как и
    «отказаться судить». Требование в том, что ТИШИНЫ быть не должно: либо
    алерт, либо запись WARNING+, называющая флаг. Молчаливый `except` здесь —
    это выключенный сторож без следа (DEV-18).
    """
    now = _at(12)
    cfg, store = _reload_scene(tmp_path, now=now, changed=bad,
                               name=f"bad{abs(hash(bad))}")
    owner = SpyNotifier()

    with caplog.at_level(logging.WARNING):
        results = _cycle(cfg, store, _miss_llm(), now=now, notifier=owner)

    assert any(r.sent for r in results), "сцена вырождена: пинг не ушёл вовсе"
    loud = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING
            and any(m in r.getMessage().lower() for m in CORRUPT_FLAG_LOG_MARKERS)]
    assert owner.cards or loud, (
        f"мусор {bad!r} в {CONFIG_CHANGED_FLAG} погасил алерт МОЛЧА: ни "
        f"карточки владельцу, ни записи WARNING+ про флаг. Весь лог: "
        f"{[(r.levelname, r.getMessage()) for r in caplog.records]}")


def test_c4_corrupt_flag_does_not_crash_the_cycle(tmp_path, flags):
    """Вторая половина того же: разбор мусора не имеет права уронить проход.
    §5 п.1 — отказ keep-alive не трогает лида; исключение, вылетевшее наружу,
    обрывает общий проход по клиентам (§6 п.4)."""
    now = _at(12)
    for bad in CORRUPT_FLAG_VALUES:
        cfg, store = _reload_scene(tmp_path, now=now, changed=bad,
                                   name=f"crash{abs(hash(bad))}")
        results = _cycle(cfg, store, _miss_llm(), now=now, notifier=SpyNotifier())
        assert results, f"мусор {bad!r} обнулил проход целиком"


# ── Г5. Гаситель не отменяет ОСТАЛЬНЫЕ породы ───────────────────────────────

def test_c5_config_change_does_not_change_the_accounting(tmp_path, flags):
    """Смена конфига внутри разрыва гасит алерт, но НЕ превращает промах в
    успех и не меняет учёт: строка пинга по-прежнему пишется под keepalive-тегом
    и НЕ считается сбоем классификатора (§5 п.2, «счётчик у алерта СВОЙ»)."""
    now = _at(12)
    cfg, store = _reload_scene(tmp_path, now=now, changed=str(_inside_gap(now)),
                               name="acct")
    before = _usage_calls(store)
    _cycle(cfg, store, _miss_llm(), now=now, notifier=SpyNotifier())

    after = _usage_calls(store)
    for combat in sorted(COMBAT_TAGS_LITERAL):
        assert after.get(combat, 0) == before.get(combat, 0), (
            f"промах под гасителем дописал строку под боевым тегом {combat!r}")
    assert classifier_failure_count(store, now=now, window_seconds=86400.0) == 0
    assert store.count_events("classifier_error", since_ts=0) == 0
