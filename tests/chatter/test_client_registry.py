"""Реестр клиентов: парсинг + валидация. Ноль ФС — проверки инжектируются."""
from __future__ import annotations

import pytest

from chatter.core.client_registry import (
    ClientEntry, RegistryError, normalize_path, parse_registry, validate,
)

ROOT = r"C:\jarvis"


def _val(entries, *, session_available=lambda p: True, client_dir_exists=lambda s: True):
    return validate(entries, root=ROOT, session_available=session_available,
                    client_dir_exists=client_dir_exists)


# ── нормализация путей ──────────────────────────────────────────────────────

@pytest.mark.parametrize("raw", [
    ".secrets/demo.session",
    ".secrets\\demo.session",
    r"C:\jarvis\.secrets\demo.session",
    r"C:\JARVIS\.secrets\DEMO.session",
    r"C:\jarvis\.secrets\..\.secrets\demo.session",
])
def test_normalize_path_collapses_equivalent_forms(raw):
    """Без этого конфликт сессий пропустит ровно тот случай, ради которого
    написан: volska пиннится на '.secrets/demo.session', а demo — на
    '.secrets\\demo.session'."""
    assert normalize_path(raw, root=ROOT) == normalize_path(
        r"C:\jarvis\.secrets\demo.session", root=ROOT)


def test_normalize_path_keeps_different_files_different():
    a = normalize_path(".secrets/demo.session", root=ROOT)
    b = normalize_path(".secrets/acme.session", root=ROOT)
    assert a != b


# ── конфликты ───────────────────────────────────────────────────────────────

def test_two_enabled_sharing_session_are_both_invalid():
    entries = (
        ClientEntry("volska", True, ("volska",), ".secrets/demo.session", ".secrets/volska.db"),
        ClientEntry("demo", True, ("demo",), ".secrets\\demo.session", ".secrets/demo.db"),
    )
    runnable, issues = _val(entries)
    assert runnable == ()
    by_slug = {i.slug: i.error for i in issues}
    assert set(by_slug) == {"volska", "demo"}
    assert "demo" in by_slug["volska"] and "session" in by_slug["volska"]
    assert "volska" in by_slug["demo"] and "session" in by_slug["demo"]


def test_two_enabled_sharing_db_are_both_invalid():
    entries = (
        ClientEntry("a", True, ("a",), ".secrets/a.session", ".secrets/shared.db"),
        ClientEntry("b", True, ("b",), ".secrets/b.session", ".secrets/shared.db"),
    )
    runnable, issues = _val(entries)
    assert runnable == ()
    assert {i.slug for i in issues} == {"a", "b"}
    assert all("db" in i.error for i in issues)


def test_conflict_with_disabled_client_is_not_a_conflict():
    """Сегодняшняя реальность: volska живёт на demo.session, а demo выключен.
    Эта пара ОБЯЗАНА быть валидной, иначе арка ломает прод в первый же цикл."""
    entries = (
        ClientEntry("volska", True, ("volska",), ".secrets/demo.session", ".secrets/demo.db"),
        ClientEntry("demo", False, ("demo", "demo2"), ".secrets/demo.session", ".secrets/demo.db"),
    )
    runnable, issues = _val(entries)
    assert [e.slug for e in runnable] == ["volska"]
    assert issues == ()


def test_third_client_runs_despite_conflict_between_first_two():
    entries = (
        ClientEntry("a", True, ("a",), ".secrets/shared.session", ".secrets/a.db"),
        ClientEntry("b", True, ("b",), ".secrets/shared.session", ".secrets/b.db"),
        ClientEntry("c", True, ("c",), ".secrets/c.session", ".secrets/c.db"),
    )
    runnable, issues = _val(entries)
    assert [e.slug for e in runnable] == ["c"]
    assert {i.slug for i in issues} == {"a", "b"}


def test_three_way_conflict_names_both_others():
    entries = tuple(
        ClientEntry(s, True, (s,), ".secrets/shared.session", f".secrets/{s}.db")
        for s in ("a", "b", "c")
    )
    runnable, issues = _val(entries)
    assert runnable == ()
    by_slug = {i.slug: i.error for i in issues}
    assert "b" in by_slug["a"] and "c" in by_slug["a"]


# ── прочая валидация ────────────────────────────────────────────────────────

def test_missing_session_file_is_explicit_error_not_silent():
    """Иначе Telethon уйдёт в интерактивный запрос кода и повиснет навсегда."""
    entries = (ClientEntry("acme", True, ("acme",), ".secrets/acme.session", ".secrets/acme.db"),)
    runnable, issues = _val(entries, session_available=lambda p: False)
    assert runnable == ()
    assert len(issues) == 1
    assert "session" in issues[0].error and "not available" in issues[0].error


def test_unknown_client_dir_is_error():
    entries = (ClientEntry("ghost", True, ("ghost",), ".secrets/ghost.session", ".secrets/ghost.db"),)
    runnable, issues = _val(entries, client_dir_exists=lambda s: False)
    assert runnable == ()
    assert "ghost" in issues[0].error


def test_empty_personas_is_error():
    entries = (ClientEntry("acme", True, (), ".secrets/acme.session", ".secrets/acme.db"),)
    runnable, issues = _val(entries)
    assert runnable == ()
    assert "personas" in issues[0].error


def test_disabled_client_is_never_validated():
    """Выключенный клиент с несуществующей сессией — не ошибка, а просто выключенный."""
    entries = (ClientEntry("old", False, (), "", ""),)
    runnable, issues = _val(entries, session_available=lambda p: False,
                            client_dir_exists=lambda s: False)
    assert runnable == ()
    assert issues == ()


def test_issues_are_deterministically_ordered():
    entries = tuple(
        ClientEntry(s, True, (s,), ".secrets/shared.session", f".secrets/{s}.db")
        for s in ("z", "a", "m")
    )
    _, issues = _val(entries)
    assert [i.slug for i in issues] == ["a", "m", "z"]


# ── парсинг ─────────────────────────────────────────────────────────────────

def test_parse_minimal_entry_derives_defaults():
    """Новый клиент = две строки. Пути выводятся из slug'а, как в раннере."""
    entries = parse_registry("clients:\n  acme:\n    enabled: true\n")
    assert entries == (
        ClientEntry("acme", True, ("acme",), ".secrets/acme.session", ".secrets/acme.db"),
    )


def test_parse_explicit_pins_win():
    text = (
        "clients:\n"
        "  volska:\n"
        "    enabled: true\n"
        "    personas: [volska]\n"
        "    session: .secrets/demo.session\n"
        "    db: .secrets/demo.db\n"
    )
    (e,) = parse_registry(text)
    assert e.session == ".secrets/demo.session"
    assert e.db == ".secrets/demo.db"


def test_parse_multi_persona_entry():
    text = "clients:\n  demo:\n    enabled: false\n    personas: [demo, demo2]\n"
    (e,) = parse_registry(text)
    assert e.personas == ("demo", "demo2")


def test_parse_enabled_defaults_to_false():
    """Безопасный дефолт: забытый enabled не поднимает клиента молча."""
    (e,) = parse_registry("clients:\n  acme: {}\n")
    assert e.enabled is False


@pytest.mark.parametrize("text,fragment", [
    ("clients: [a, b]\n", "словарём"),
    ("nope: 1\n", "clients"),
    ("clients:\n  acme: 5\n", "acme"),
    ("clients:\n  acme:\n   - broken\n  : :\n", "не парсится"),
])
def test_broken_registry_raises_registry_error(text, fragment):
    with pytest.raises(RegistryError) as exc:
        parse_registry(text)
    assert fragment in str(exc.value)
