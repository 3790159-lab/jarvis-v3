"""Периодический авто-возврат. Тестируется ОДИН прогон (sweep), а не цикл."""
from __future__ import annotations

from chatter.storage.db import Store
from chatter.telethon_run import autoresume_sweep

HOUR = 3600.0


def test_sweep_resumes_an_expired_command_pause():
    with Store(":memory:") as s:
        s.get_or_create_contact("c1")
        s.mute("c1", source="command", until=200.0, now=100.0)
        autoresume_sweep(s, now=201.0, auto_resume_hours=6)
        assert s.get_or_create_contact("c1")["paused"] == 0


def test_sweep_leaves_a_fresh_takeover_alone():
    with Store(":memory:") as s:
        s.get_or_create_contact("c1")
        s.mute("c1", source="human_takeover", msg_id=1, now=100.0)
        s.note_human_out("c1", ts=100.0)
        autoresume_sweep(s, now=100.0 + 300, auto_resume_hours=6)
        assert s.get_or_create_contact("c1")["paused"] == 1


def test_sweep_writes_its_heartbeat_every_run():
    # Мёртвая задача выглядит РОВНО как «пауз к возврату нет». Единственная
    # разница — этот heartbeat, и /status обязан его показывать.
    with Store(":memory:") as s:
        autoresume_sweep(s, now=1234.0, auto_resume_hours=6)
        assert s.get_runtime_flag("autoresume_beat") == "1234.0"


def test_sweep_never_touches_the_kill_switch():
    # Рубильник снимает только владелец.
    with Store(":memory:") as s:
        s.set_runtime_flag("kill_switch", "1", ts=100.0)
        autoresume_sweep(s, now=10_000 * HOUR, auto_resume_hours=6)
        assert s.get_runtime_flag("kill_switch") == "1"


def test_sweep_writes_heartbeat_even_if_one_contact_blows_up():
    # ФАКТ-ПРОВЕРКА против плана: план писал heartbeat одной строкой ПОСЛЕ
    # цикла размораживания. Если unmute() на какой-то одной строке кидает
    # исключение (сбой БД, гонка с ручным /resume той же секундой), план-код
    # унёс бы выполнение мимо строки с heartbeat -- он бы не записался, и
    # /status начал бы врать "задача жива", хотя sweep застрял на первой же
    # сломанной строке. Это тот же класс тихого вранья, от которого вся
    # идея heartbeat.
    with Store(":memory:") as s:
        s.get_or_create_contact("c1")
        s.mute("c1", source="command", until=200.0, now=100.0)

        def boom(contact_id):
            raise RuntimeError("db exploded mid-unmute")
        s.unmute = boom  # type: ignore[method-assign]

        autoresume_sweep(s, now=201.0, auto_resume_hours=6)
        assert s.get_runtime_flag("autoresume_beat") == "201.0"


def test_sweep_continues_past_a_broken_row_to_resume_the_rest():
    # Одна порченая строка не имеет права остановить размораживание ВСЕХ
    # остальных диалогов, идущих за ней -- иначе они залипнут молча до
    # следующего /resume руками, а sweep будет выглядеть как отработавший
    # нормально (heartbeat свежий, но фактически ничего после c1 не делалось).
    with Store(":memory:") as s:
        s.get_or_create_contact("c1")
        s.get_or_create_contact("c2")
        s.mute("c1", source="command", until=200.0, now=100.0)
        s.mute("c2", source="command", until=200.0, now=100.0)

        real_unmute = s.unmute

        def flaky(contact_id):
            if contact_id == "c1":
                raise RuntimeError("boom")
            real_unmute(contact_id)
        s.unmute = flaky  # type: ignore[method-assign]

        resumed = autoresume_sweep(s, now=201.0, auto_resume_hours=6)
        assert s.get_or_create_contact("c1")["paused"] == 1   # осталась заглушена — sweep не смог её снять
        assert s.get_or_create_contact("c2")["paused"] == 0   # но это не остановило остальных
        assert resumed == 1
