"""Исполнение команд пульта над store. Telethon не участвует."""
from __future__ import annotations

from chatter.core.console import Command
from chatter.storage.db import Store
from chatter.telethon_run import execute_command, resolve_numbered_target


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


# ---------------------------------------------------------------------------
# Задача 3 под-арки 3A-UX: /help, и различение номера/id при адресации
# /resume и /pause (спека §3, §6). resolve_numbered_target — ЧИСТАЯ функция
# (только store), поэтому тестируется здесь, а не живым Telethon.
# ---------------------------------------------------------------------------

def test_help_returns_the_full_help_text():
    with Store(":memory:") as s:
        out = execute_command(Command(name="help"), store=s, contact_id=None, now=100.0)
        assert "/status" in out and "/pause" in out and "/resume" in out


def test_resolve_numbered_target_finds_the_contact_from_the_last_status_snapshot():
    # /resume 1 -- номер из /status, НЕ id. resolve_numbered_target читает
    # его из status_index, а не пытается угадать по величине числа (спека
    # §3: "не угадывай по величине — это хрупко").
    with Store(":memory:") as s:
        s.get_or_create_contact("237616472:demo")
        s.mute("237616472:demo", source="human_takeover", now=100.0)
        s.issue_status_index(["237616472:demo"], now=100.0)
        contact_id, error = resolve_numbered_target(s, "1", language="ru")
        assert contact_id == "237616472:demo"
        assert error is None


def test_resolve_numbered_target_blocks_on_a_stale_list_and_says_so():
    # Владелец смотрел /status час назад, список с тех пор изменился (кто-то
    # авто-вернулся/добавился новый) -- промах в чужой диалог = катастрофа
    # доверия (спека §3), поэтому НЕ резолвим, а объясняем.
    with Store(":memory:") as s:
        s.get_or_create_contact("c1")
        s.mute("c1", source="human_takeover", now=100.0)
        s.issue_status_index(["c1"], now=100.0)
        # Список успел измениться: c2 тоже заглушён, но /status не переиздан.
        s.get_or_create_contact("c2")
        s.mute("c2", source="human_takeover", now=150.0)
        contact_id, error = resolve_numbered_target(s, "1", language="ru")
        assert contact_id is None
        assert error is not None
        assert "устарел" in error


def test_resolve_numbered_target_stale_list_is_not_executed_via_execute_command():
    # Сквозная проверка: ошибка устаревшего списка доходит до
    # execute_command и БЛОКИРУЕТ действие -- диалог c1 остаётся заглушён.
    with Store(":memory:") as s:
        s.get_or_create_contact("c1")
        s.mute("c1", source="human_takeover", now=100.0)
        s.issue_status_index(["c1"], now=100.0)
        s.get_or_create_contact("c2")
        s.mute("c2", source="human_takeover", now=150.0)
        contact_id, error = resolve_numbered_target(s, "1", language="ru")
        out = execute_command(Command(name="resume", target="1"), store=s,
                              contact_id=contact_id, now=200.0, target_error=error)
        assert "устарел" in out
        assert s.get_or_create_contact("c1")["paused"] == 1   # НЕ снята


def test_resolve_numbered_target_ignores_a_non_numeric_target():
    # /resume @username -- не номер, resolve_numbered_target не вмешивается,
    # (None, None) сигналит "это не моя забота", вызывающий код (resolve_target
    # в раннере) резолвит как раньше через username/ссылку/id.
    with Store(":memory:") as s:
        contact_id, error = resolve_numbered_target(s, "@daniil", language="ru")
        assert contact_id is None
        assert error is None


def test_resolve_numbered_target_unknown_number_falls_through_silently():
    # Номер, которого нет в таблице -- ЦИФРОВАЯ строка тоже "похожа на id"
    # (спека §3), поэтому НЕ ошибка здесь: (None, None) значит "попробуй
    # старый путь резолва" (id/ссылка), а не "нет такого номера".
    with Store(":memory:") as s:
        contact_id, error = resolve_numbered_target(s, "999", language="ru")
        assert contact_id is None
        assert error is None
