"""Исполнение команд пульта над store. Telethon не участвует."""
from __future__ import annotations

from chatter.core.console import Command
from chatter.storage.db import Store
from chatter.telethon_run import execute_command


def test_stop_sets_the_kill_switch_and_start_clears_it():
    with Store(":memory:") as s:
        out = execute_command(Command(name="stop"), store=s, contact_id=None, now=100.0)
        assert s.get_runtime_flag("kill_switch") == "1"
        assert "заглушена" in out.casefold()
        execute_command(Command(name="start"), store=s, contact_id=None, now=200.0)
        assert s.get_runtime_flag("kill_switch") == "0"


def test_resume_by_reply_unmutes_that_dialog():
    with Store(":memory:") as s:
        s.get_or_create_contact("c1")
        s.mute("c1", source="human_takeover", msg_id=1, now=100.0)
        execute_command(Command(name="resume"), store=s, contact_id="c1", now=200.0)
        assert s.get_or_create_contact("c1")["paused"] == 0


def test_pause_with_duration_sets_a_deadline():
    with Store(":memory:") as s:
        s.get_or_create_contact("c1")
        execute_command(Command(name="pause", duration_seconds=3600.0),
                        store=s, contact_id="c1", now=100.0)
        assert s.get_or_create_contact("c1")["pause_until"] == 3700.0


def test_targeted_command_without_a_target_explains_itself_instead_of_going_global():
    # «Хотел притормозить один диалог, а заглушил всю воронку» — слишком
    # дорогая опечатка. Глобальное глушение называется /stop.
    with Store(":memory:") as s:
        out = execute_command(Command(name="pause"), store=s, contact_id=None, now=100.0)
        assert "реплаем" in out
        assert s.muted_contacts() == []


def test_parser_error_is_shown_to_the_owner():
    with Store(":memory:") as s:
        out = execute_command(Command(name="pause", error="не понял длительность: 'x'"),
                              store=s, contact_id="c1", now=100.0)
        assert "не понял длительность" in out
        assert s.muted_contacts() == []


def test_pause_creates_the_contact_row_if_ania_never_touched_it():
    # ФАКТ-ПРОВЕРКА против плана: Store.mute() кидает KeyError на
    # несуществующем contact_id (db.py, коммит f060594) -- план этого не
    # учитывал изначально. Владелец имеет право упредить Аню и заглушить
    # диалог, которого она ещё не касалась (например /pause <ссылка> на
    # лида, которому только собирается написать), поэтому execute_command
    # ОБЯЗАН создать строку до mute(), а не дать владельцу необъяснённый
    # краш вместо "поставил на паузу".
    with Store(":memory:") as s:
        assert s.has_contact("999:demo") is False
        out = execute_command(Command(name="pause"), store=s, contact_id="999:demo", now=100.0)
        assert "заглуш" in out.casefold()
        assert s.get_or_create_contact("999:demo")["paused"] == 1
