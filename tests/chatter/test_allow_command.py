"""/allow (контрол-бот): добавление/удаление контакта в always-answer
allowlist по @username, числовому id или пересланному сообщению, с
подтверждением мутирующего действия. funnel_gate НЕ трогается — это
отдельный runtime-оверлей поверх settings.yaml.telegram.allowlist,
персистентный в Store (см. arc3c-contact-gate-design.md «Out of scope»).
"""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from chatter.config.loader import load_config
from chatter.core.brain import Brain
from chatter.core.console import AllowCommand, parse_allow_command
from chatter.core.llm import FakeLLM
from chatter.run import Deps
from chatter.storage.db import Store
from chatter.telethon_run import PersonaBundle, TelethonRunner

CLIENTS_DIR = Path(__file__).resolve().parents[2] / "chatter" / "clients"
STATIC_ID = 237616472   # уже в chatter/clients/demo/settings.yaml allowlist

# --- чистый парсер аргументов -----------------------------------------------


def test_no_arg_is_missing_target():
    cmd = parse_allow_command("")
    assert cmd == AllowCommand(action="add", target=None, confirmed=False)


def test_list_keyword():
    assert parse_allow_command("list") == AllowCommand(action="list", target=None, confirmed=False)
    assert parse_allow_command("  list  ") == AllowCommand(action="list", target=None, confirmed=False)


def test_bare_id_is_add_unconfirmed():
    cmd = parse_allow_command("237616472")
    assert cmd == AllowCommand(action="add", target="237616472", confirmed=False)


def test_id_with_confirm():
    cmd = parse_allow_command("237616472 confirm")
    assert cmd == AllowCommand(action="add", target="237616472", confirmed=True)


def test_username_target():
    cmd = parse_allow_command("@daniil")
    assert cmd == AllowCommand(action="add", target="@daniil", confirmed=False)


def test_remove_keyword():
    cmd = parse_allow_command("remove 237616472")
    assert cmd == AllowCommand(action="remove", target="237616472", confirmed=False)


def test_remove_with_confirm():
    cmd = parse_allow_command("remove 237616472 confirm")
    assert cmd == AllowCommand(action="remove", target="237616472", confirmed=True)


def test_remove_without_target_is_missing_target():
    cmd = parse_allow_command("remove")
    assert cmd == AllowCommand(action="remove", target=None, confirmed=False)


def test_remove_confirm_without_target_still_confirmed_but_no_target():
    """Реплай на пересланное сообщение с '/allow remove confirm' -- цель
    подставит вызывающая сторона (control_bot._resolve_allow_arg), здесь
    только чистый разбор токенов."""
    cmd = parse_allow_command("remove confirm")
    assert cmd == AllowCommand(action="remove", target=None, confirmed=True)


def test_ru_confirm_synonyms_recognized():
    for word in ("да", "yes", "так"):
        assert parse_allow_command(f"1 {word}").confirmed is True


# --- TelethonRunner.handle_config_command("allow", ...) --------------------


def _entity(uid: int, *, username: str | None = None, first_name: str | None = None):
    e = MagicMock()
    e.id = uid
    e.username = username
    e.first_name = first_name
    e.last_name = None
    e.title = None
    return e


def _bundle(slug, store):
    cfg = load_config(CLIENTS_DIR, slug)
    deps = Deps(cfg=cfg, store=store, brain=Brain(FakeLLM(), cfg),
                rng=random.Random(0), clock=time.time, sleep=lambda s: None)
    return PersonaBundle(cfg=cfg, deps=deps)


def _runner(*, store=None, get_entity=None):
    store = store or Store(":memory:")
    client = MagicMock()
    client.get_entity = get_entity or AsyncMock(side_effect=ValueError("not cached"))
    r = TelethonRunner(
        client=client, personas={"demo": _bundle("demo", store)}, primary_slug="demo",
        allowlist=frozenset({STATIC_ID}), loop=asyncio.new_event_loop())
    return r


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_allow_usage_on_missing_target():
    r = _runner()
    out = _run(r.handle_config_command("allow", "", language="ru"))
    assert "@user" in out and "id" in out


def test_allow_add_first_call_shows_confirm_and_does_not_mutate():
    r = _runner()
    out = _run(r.handle_config_command("allow", "111222", language="ru"))
    assert "confirm" in out.lower()
    assert r.primary_store().runtime_allow_ids() == []


def test_allow_add_confirmed_persists_and_feeds_effective_allowlist():
    r = _runner()
    out = _run(r.handle_config_command("allow", "111222 confirm", language="ru"))
    assert "✅" in out
    assert r.primary_store().runtime_allow_ids() == [111222]
    assert 111222 in r.effective_allowlist()
    assert STATIC_ID in r.effective_allowlist()   # старый allowlist никуда не делся


def test_allow_add_confirmed_twice_says_already_in_list():
    r = _runner()
    _run(r.handle_config_command("allow", "111222 confirm", language="ru"))
    out = _run(r.handle_config_command("allow", "111222 confirm", language="ru"))
    assert "уже" in out.lower()
    assert r.primary_store().runtime_allow_ids() == [111222]


def test_allow_remove_of_static_only_id_is_blocked_not_silently_ignored():
    """id живёт ТОЛЬКО в settings.yaml -- /allow remove не может его убрать
    (нет reload/yaml-правки в этой фиче), и ОБЯЗАН сказать это честно
    (DEV-18), а не соврать "убрал"."""
    r = _runner()
    out = _run(r.handle_config_command("allow", f"remove {STATIC_ID} confirm", language="ru"))
    assert "settings.yaml" in out
    assert STATIC_ID in r.effective_allowlist()   # НЕ убран


def test_allow_remove_runtime_added_id_confirmed_removes():
    r = _runner()
    _run(r.handle_config_command("allow", "111222 confirm", language="ru"))
    out = _run(r.handle_config_command("allow", "remove 111222 confirm", language="ru"))
    assert "✅" in out
    assert 111222 not in r.effective_allowlist()
    assert r.primary_store().runtime_allow_ids() == []


def test_allow_remove_requires_confirmation_first():
    r = _runner()
    _run(r.handle_config_command("allow", "111222 confirm", language="ru"))
    out = _run(r.handle_config_command("allow", "remove 111222", language="ru"))
    assert "confirm" in out.lower()
    assert 111222 in r.effective_allowlist()   # ещё не убран


def test_allow_remove_id_not_in_list_at_all():
    r = _runner()
    out = _run(r.handle_config_command("allow", "999999 confirm", language="ru"))
    assert "999999" in out or "add" in out.lower()  # добавился (confirm сразу сработал)
    out2 = _run(r.handle_config_command("allow", "remove 555555 confirm", language="ru"))
    assert "не в allowlist" in out2.lower() or "nothing" in out2.lower()


def test_allow_username_resolves_via_get_entity():
    get_entity = AsyncMock(return_value=_entity(333, username="daniil", first_name="Даниил"))
    r = _runner(get_entity=get_entity)
    out = _run(r.handle_config_command("allow", "@daniil confirm", language="ru"))
    assert "✅" in out and "Даниил" in out
    assert 333 in r.effective_allowlist()
    get_entity.assert_awaited_with("daniil")


def test_allow_unresolvable_username_returns_not_found_and_mutates_nothing():
    get_entity = AsyncMock(side_effect=ValueError("no such user"))
    r = _runner(get_entity=get_entity)
    out = _run(r.handle_config_command("allow", "@ghost confirm", language="ru"))
    assert "не смог определить" in out.lower() or "ghost" in out.lower()
    assert r.primary_store().runtime_allow_ids() == []


def test_allow_numeric_id_add_succeeds_even_if_entity_unknown():
    """get_entity падает (аккаунт никогда не видел этот id) -- добавление
    ЧИСЛОВОГО id всё равно должно сработать (id уже известен буквально),
    просто с именем-фолбэком на голый id."""
    r = _runner()   # дефолтный get_entity кидает ValueError
    out = _run(r.handle_config_command("allow", "111222 confirm", language="ru"))
    assert "✅" in out
    assert 111222 in r.effective_allowlist()


def test_allow_list_shows_static_and_runtime_with_source_labels():
    r = _runner()
    _run(r.handle_config_command("allow", "111222 confirm", language="ru"))
    out = _run(r.handle_config_command("allow", "list", language="ru"))
    assert str(STATIC_ID) in out and "settings.yaml" in out
    assert "111222" in out and "/allow" in out


def test_allow_list_empty_when_no_allowlist_at_all():
    r = _runner()
    r.allowlist = frozenset()
    out = _run(r.handle_config_command("allow", "list", language="ru"))
    assert "пуст" in out.lower()


@pytest.mark.parametrize("lang", ["ru", "en", "uk"])
def test_all_allow_languages_have_strings(lang):
    r = _runner()
    for arg in ("", "list", "111222", "111222 confirm", f"remove {STATIC_ID} confirm",
                "@ghost confirm"):
        out = _run(r.handle_config_command("allow", arg, language=lang))
        assert out and "{" not in out


# --- admission wiring: /allow действительно меняет, кому отвечает Аня ------


@dataclass
class _Sender:
    bot: bool = False
    contact: bool = False


@dataclass
class _Event:
    sender_id: int
    raw_text: str = "привет"
    chat_id: int | None = None
    is_private: bool = True
    out: bool = False
    sender: _Sender = field(default_factory=_Sender)
    action: object = None

    def __post_init__(self):
        if self.chat_id is None:
            self.chat_id = self.sender_id

    async def get_input_chat(self):
        return f"inputpeer:{self.chat_id}"


def test_allow_confirmed_add_lets_handle_event_answer_a_new_sender():
    async def scenario():
        r = _runner()
        r.me_id = 111
        await r.handle_event(_Event(sender_id=555444))
        await asyncio.sleep(0)
        assert 555444 not in r._debouncers   # ещё не в allowlist

        await r.handle_config_command("allow", "555444 confirm", language="ru")
        await r.handle_event(_Event(sender_id=555444))
        await asyncio.sleep(0)
        assert 555444 in r._debouncers       # теперь /allow пускает его как лида
    asyncio.run(scenario())
