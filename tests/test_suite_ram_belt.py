# -*- coding: utf-8 -*-
"""Сторожа ремня по памяти для ручного `pytest tests/`.

Написаны ОТ СПЕКИ `docs/superpowers/specs/2026-08-21-suite-ram-belt.md` §6,
до реализации. Решающая логика обязана быть чистой функцией от чисел и
времени: ремень, который нельзя проверить, не воспроизводя аварию, — это не
сторож, а надежда.
"""
from __future__ import annotations

import pytest

from app.services.suite_ram_belt import (
    ACTION_EXIT,
    ACTION_INTERRUPT,
    ACTION_OK,
    ACTION_WAIT,
    DEFAULT_EXIT_CODE,
    DEFAULT_HARD_GRACE_S,
    DEFAULT_KILL_FREE_GB,
    DEFAULT_KILL_RSS_GB,
    DEFAULT_SAMPLE_S,
    Belt,
    Limits,
    limits_from_env,
    render_report,
)


def _limits(**kw) -> Limits:
    base = dict(free_gb=2.5, rss_gb=3.0, sample_s=2.0, grace_s=20.0,
                hard_rss_gb=6.0, hard_free_gb=1.0,
                enabled=True, exit_code=77)
    base.update(kw)
    return Limits(**base)


# ── §3.1 третий зуб: отсрочка по ВРЕМЕНИ бессмысленна при быстром росте ──────
#
# Найдено живым дрилом 21.08, а не рассуждением. Распаковка нулей росла
# ~2 ГБ/с: за отсрочку в 3 с процесс ушёл с 2.1 до 4.7 ГБ. На течи 21.08
# (+190 МБ/с) умолчание в 20 с стоит +3.8 ГБ — терпимо; при 2 ГБ/с те же 20 с
# это +40 ГБ, и машина умрёт раньше, чем сработает backstop.
#
# Значит нужна линия, при пересечении которой ждать нельзя вообще.


def test_rss_far_above_the_cap_exits_immediately_without_waiting():
    belt = Belt(_limits(rss_gb=3.0, hard_rss_gb=6.0, grace_s=20.0))

    assert belt.observe(now=0.0, free_gb=8.0, rss_gb=6.1) == ACTION_EXIT


def test_free_ram_far_below_the_floor_exits_immediately_without_waiting():
    belt = Belt(_limits(free_gb=2.5, hard_free_gb=1.0, grace_s=20.0))

    assert belt.observe(now=0.0, free_gb=0.9, rss_gb=0.5) == ACTION_EXIT


def test_hard_lines_are_strict_too():
    """Ровно НА линии — ещё мягкий путь: пусть pytest успеет назвать тест."""
    belt = Belt(_limits(rss_gb=3.0, hard_rss_gb=6.0, hard_free_gb=1.0))

    assert belt.observe(now=0.0, free_gb=1.0, rss_gb=6.0) == ACTION_INTERRUPT


def test_hard_lines_ignore_the_disabled_belt_too():
    belt = Belt(_limits(enabled=False))

    assert belt.observe(now=0.0, free_gb=0.01, rss_gb=99.0) == ACTION_OK


def test_hard_line_defaults_are_literal():
    from app.services.suite_ram_belt import (
        DEFAULT_HARD_FREE_GB,
        DEFAULT_HARD_RSS_GB,
    )

    assert DEFAULT_HARD_RSS_GB == 6.0
    assert DEFAULT_HARD_FREE_GB == 1.0


def test_hard_lines_are_env_overridable():
    lim = limits_from_env({"SUITE_HARD_RSS_GB": "2.5", "SUITE_HARD_FREE_GB": "0.4"})

    assert lim.hard_rss_gb == 2.5
    assert lim.hard_free_gb == 0.4


def test_report_names_the_hard_line_when_the_hard_line_fired():
    """Живой дрил дал два отчёта подряд, и ОБА назвали мягкий потолок 0.80 —
    хотя второй сработал по жёсткой линии 2.0. Читающий отчёт должен видеть,
    какой зуб сработал, иначе он будет крутить не тот порог."""
    from app.services.suite_ram_belt import REASON_RSS

    text = render_report(current_test="tests/x.py::t", free_gb=8.0, rss_gb=9.9,
                         elapsed_s=1.0, limits=_limits(rss_gb=3.0, hard_rss_gb=6.0),
                         reason=REASON_RSS, hard=True)

    assert "6.0" in text
    assert "3.0" not in text.split("пробито")[1].split("\n")[0]


def test_belt_says_whether_the_exit_was_hard():
    belt = Belt(_limits(rss_gb=3.0, hard_rss_gb=6.0, grace_s=20.0))
    assert belt.observe(now=0.0, free_gb=8.0, rss_gb=9.9) == ACTION_EXIT
    assert belt.hard is True

    slow = Belt(_limits(rss_gb=3.0, hard_rss_gb=6.0, grace_s=20.0))
    slow.observe(now=0.0, free_gb=8.0, rss_gb=3.5)
    assert slow.observe(now=25.0, free_gb=8.0, rss_gb=3.5) == ACTION_EXIT
    assert slow.hard is False


def test_hard_exit_from_the_hard_line_still_reports_first():
    """Без отчёта аварийный выход — это молчаливая смерть прогона, то есть
    ровно та тишина, ради устранения которой ремень и заведён."""
    spy = _Spy()
    s = _sampler(spy, [(0.0, 8.0, 9.9)], limits=_limits(hard_rss_gb=6.0))

    assert s.tick() == ACTION_EXIT
    assert spy.names() == ["emit", "die"]


# ── §6.1-3: когда зуб 1 срабатывает ──────────────────────────────────────────


def test_both_values_healthy_means_no_action():
    belt = Belt(_limits())

    assert belt.observe(now=0.0, free_gb=8.0, rss_gb=0.8) == ACTION_OK


def test_free_ram_below_floor_interrupts():
    """Пол по свободной памяти — тот же случай, что ловил июльский run_guarded."""
    belt = Belt(_limits())

    assert belt.observe(now=0.0, free_gb=2.4, rss_gb=0.8) == ACTION_INTERRUPT


def test_own_rss_above_cap_interrupts():
    """Потолок по своему RSS ловит течь РАНЬШЕ, чем просядет система.

    Замер 21.08: пик здоровой суиты 801 МБ, скорость течи +190 МБ/с. Порог по
    RSS ловит её на ~16-й секунде, порог по свободной памяти — на ~110-й.
    """
    belt = Belt(_limits())

    assert belt.observe(now=0.0, free_gb=9.0, rss_gb=3.1) == ACTION_INTERRUPT


def test_value_exactly_on_the_boundary_is_not_a_breach():
    """Граница строгая: сторож, загорающийся ровно на пороге, приучает к тому,
    что он слегка врёт."""
    belt = Belt(_limits())

    assert belt.observe(now=0.0, free_gb=2.5, rss_gb=3.0) == ACTION_OK


# ── §6.4-5: второй зуб и дребезг ─────────────────────────────────────────────


def test_breach_that_outlives_the_grace_escalates_to_hard_exit():
    """Прерывание не взялось — значит главный поток в C-коде и байт-код не
    исполняет. Тогда аварийный выход."""
    belt = Belt(_limits(grace_s=20.0))

    assert belt.observe(now=0.0,  free_gb=1.0, rss_gb=5.0) == ACTION_INTERRUPT
    assert belt.observe(now=10.0, free_gb=1.0, rss_gb=6.0) == ACTION_WAIT
    assert belt.observe(now=20.0, free_gb=1.0, rss_gb=7.0) == ACTION_EXIT


def test_breach_that_clears_resets_the_countdown():
    """§3.3 дребезг: всплеск в начале прогона и настоящая течь через двадцать
    минут НЕ должны складываться, иначе второй зуб сработает мгновенно и
    отнимет шанс на штатное разматывание."""
    belt = Belt(_limits(grace_s=20.0))

    assert belt.observe(now=0.0, free_gb=1.0, rss_gb=0.5) == ACTION_INTERRUPT
    assert belt.observe(now=5.0, free_gb=8.0, rss_gb=0.5) == ACTION_OK

    # спустя двадцать минут — НОВОЕ пересечение, а не продолжение старого
    assert belt.observe(now=1200.0, free_gb=1.0, rss_gb=0.5) == ACTION_INTERRUPT
    assert belt.observe(now=1205.0, free_gb=1.0, rss_gb=0.5) == ACTION_WAIT


def test_interrupt_is_issued_once_per_breach_not_every_sample():
    """Иначе главный поток получит KeyboardInterrupt столько раз, сколько
    успеет сделать сэмплер, и разматывание сорвётся на полпути."""
    belt = Belt(_limits(grace_s=20.0))

    assert belt.observe(now=0.0, free_gb=1.0, rss_gb=0.5) == ACTION_INTERRUPT
    assert belt.observe(now=2.0, free_gb=1.0, rss_gb=0.5) == ACTION_WAIT
    assert belt.observe(now=4.0, free_gb=1.0, rss_gb=0.5) == ACTION_WAIT


# ── §6.6: отчёт ──────────────────────────────────────────────────────────────


def test_report_names_the_running_test():
    """То, ради чего ремень живёт ВНУТРИ pytest, а не в обёртке: снаружи имя
    теста недоступно, и 21.08 его добывали час."""
    text = render_report(current_test="tests/test_state_backup.py::test_run_backup_uploads",
                         free_gb=1.2, rss_gb=4.7, elapsed_s=931.0,
                         limits=_limits(), reason="rss")

    assert "tests/test_state_backup.py::test_run_backup_uploads" in text


def test_report_names_which_threshold_was_crossed_and_both_values():
    text = render_report(current_test="tests/t.py::t", free_gb=1.2, rss_gb=4.7,
                         elapsed_s=931.0, limits=_limits(), reason="rss")

    assert "1.2" in text          # свободная память
    assert "4.7" in text          # свой RSS
    assert "3.0" in text          # пробитый порог
    assert "rss" in text.lower()  # какой именно


def test_report_says_when_no_test_was_running():
    """Течь может случиться на сборе или в фикстуре сессии — «неизвестно»
    должно быть сказано вслух, а не подставлено пустой строкой."""
    text = render_report(current_test=None, free_gb=1.2, rss_gb=4.7,
                         elapsed_s=5.0, limits=_limits(), reason="free")

    assert text.strip()
    assert "None" not in text


# ── §6.7: умолчания не разъезжаются с июльским ремнём ────────────────────────


def test_free_ram_default_is_literally_two_and_a_half():
    assert DEFAULT_KILL_FREE_GB == 2.5


def test_free_ram_default_equals_the_july_batch_belt():
    """Два ремня на одну машину обязаны иметь ОДИН пол по памяти.

    Сверка с чужой константой, а не с самим собой: выведенный из своего же
    кода список согласен с кодом по определению и молчит там, где тот забыл.
    """
    from app.services.devtask.regress_batches import DEFAULT_BATCH_KILL_FREE_GB

    assert DEFAULT_KILL_FREE_GB == DEFAULT_BATCH_KILL_FREE_GB


def test_remaining_defaults_are_literal():
    assert DEFAULT_KILL_RSS_GB == 3.0
    assert DEFAULT_SAMPLE_S == 2.0
    assert DEFAULT_HARD_GRACE_S == 20.0
    assert DEFAULT_EXIT_CODE == 77


# ── §6.8-9: среда ────────────────────────────────────────────────────────────


def test_env_overrides_every_threshold():
    lim = limits_from_env({
        "SUITE_KILL_FREE_GB": "1.25",
        "SUITE_KILL_RSS_GB": "9",
        "SUITE_SAMPLE_S": "0.5",
        "SUITE_HARD_GRACE_S": "3",
        "SUITE_RAM_EXIT_CODE": "42",
    })

    assert lim.free_gb == 1.25
    assert lim.rss_gb == 9.0
    assert lim.sample_s == 0.5
    assert lim.grace_s == 3.0
    assert lim.exit_code == 42


def test_empty_env_gives_the_documented_defaults():
    lim = limits_from_env({})

    assert lim.enabled is True
    assert lim.free_gb == DEFAULT_KILL_FREE_GB
    assert lim.rss_gb == DEFAULT_KILL_RSS_GB


def test_belt_switches_off_only_on_explicit_zero():
    assert limits_from_env({"SUITE_RAM_BELT": "0"}).enabled is False
    assert limits_from_env({"SUITE_RAM_BELT": "1"}).enabled is True
    assert limits_from_env({}).enabled is True


def test_garbage_in_env_is_loud_not_silently_ignored():
    """Проглоченная опечатка в пороге = ремень с другим порогом, о котором
    никто не знает. DEV-18: не глотать."""
    with pytest.raises(ValueError):
        limits_from_env({"SUITE_KILL_FREE_GB": "два с половиной"})


def test_disabled_belt_never_acts():
    belt = Belt(_limits(enabled=False))

    assert belt.observe(now=0.0, free_gb=0.1, rss_gb=99.0) == ACTION_OK


# ── обвязка: сэмплер, порядок действий, слежение за текущим тестом ───────────
#
# Съём величин, часы, прерывание и аварийный выход — всё внедряется. Иначе
# проверить сэмплер можно только настоящей аварией, а это ровно то, чего
# ремень и должен не допускать.


class _Spy:
    """Пишет ПОРЯДОК вызовов, а не только факт: `os._exit` не сбрасывает
    буферы, поэтому «отчёт после выхода» = отчёта нет."""

    def __init__(self):
        self.calls = []

    def emit(self, text):
        self.calls.append(("emit", text))

    def interrupt(self):
        self.calls.append(("interrupt", None))

    def die(self, code):
        self.calls.append(("die", code))

    def names(self):
        return [c[0] for c in self.calls]


def _sampler(spy, readings, *, limits=None, current_test="tests/t.py::t"):
    from app.services.suite_ram_belt import Sampler

    clock = iter([r[0] for r in readings])
    values = iter([(r[1], r[2]) for r in readings])
    return Sampler(
        Belt(limits or _limits()),
        read=lambda: next(values),
        clock=lambda: next(clock),
        emit=spy.emit,
        interrupt=spy.interrupt,
        die=spy.die,
        current_test=lambda: current_test,
        started_at=0.0,
    )


def test_healthy_tick_does_nothing_at_all():
    spy = _Spy()
    s = _sampler(spy, [(0.0, 8.0, 0.8)])

    assert s.tick() == ACTION_OK
    assert spy.names() == []


def test_first_breach_reports_then_interrupts_in_that_order():
    spy = _Spy()
    s = _sampler(spy, [(0.0, 1.0, 0.8)])

    assert s.tick() == ACTION_INTERRUPT
    assert spy.names() == ["emit", "interrupt"]


def test_waiting_tick_does_not_report_again():
    """Повторный KeyboardInterrupt сорвёт разматывание на полпути."""
    spy = _Spy()
    s = _sampler(spy, [(0.0, 1.0, 0.8), (5.0, 1.0, 0.8)])

    s.tick()
    spy.calls.clear()

    assert s.tick() == ACTION_WAIT
    assert spy.names() == []


def test_hard_exit_reports_before_dying():
    spy = _Spy()
    s = _sampler(spy, [(0.0, 1.0, 0.8), (25.0, 1.0, 0.8)], limits=_limits(grace_s=20.0))

    s.tick()
    spy.calls.clear()

    assert s.tick() == ACTION_EXIT
    assert spy.names() == ["emit", "die"]
    assert spy.calls[-1][1] == 77          # код выхода из порогов


def test_report_carries_the_test_name_from_the_tracker():
    spy = _Spy()
    s = _sampler(spy, [(0.0, 1.0, 0.8)], current_test="tests/test_x.py::test_leak")

    s.tick()

    assert "tests/test_x.py::test_leak" in spy.calls[0][1]


def test_disabled_belt_sampler_is_inert():
    spy = _Spy()
    s = _sampler(spy, [(0.0, 0.1, 99.0)], limits=_limits(enabled=False))

    assert s.tick() == ACTION_OK
    assert spy.names() == []


def test_sampler_remembers_the_last_report():
    """Живой дрил показал: `sys.stderr` до экрана НЕ доходит — pytest
    перехватывает поток на уровне дескриптора, и при KeyboardInterrupt
    перехваченное отбрасывается. Отчёт остался только в файле.

    Значит отчёт надо запомнить, чтобы крючок `pytest_keyboard_interrupt`
    напечатал его СВОИМ каналом. Человек, гоняющий суиту руками, обязан
    увидеть имя виноватого теста на экране, а не узнать про лог-файл.
    """
    spy = _Spy()
    s = _sampler(spy, [(0.0, 1.0, 0.8)], current_test="tests/x.py::test_leak")

    assert s.last_report is None
    s.tick()

    assert s.last_report is not None
    assert "tests/x.py::test_leak" in s.last_report


def test_last_report_stays_empty_while_healthy():
    spy = _Spy()
    s = _sampler(spy, [(0.0, 8.0, 0.5)])

    s.tick()

    assert s.last_report is None


def test_current_test_tracker_follows_pytest():
    from app.services.suite_ram_belt import CurrentTest

    tracker = CurrentTest()
    assert tracker.get() is None

    tracker.set("tests/a.py::test_one")
    assert tracker.get() == "tests/a.py::test_one"

    tracker.clear()
    assert tracker.get() is None


def test_emitter_writes_to_file_and_stream_and_flushes(tmp_path):
    """Файл нужен именно потому, что аварийный выход теряет буферы: отчёт,
    видимый только в stderr, при `os._exit` может не доехать."""
    import io

    from app.services.suite_ram_belt import make_emitter

    path = tmp_path / "belt.log"
    stream = io.StringIO()
    emit = make_emitter(path, stream)

    emit("ТЕЧЬ: tests/x.py::test_y")

    assert "tests/x.py::test_y" in path.read_text(encoding="utf-8")
    assert "tests/x.py::test_y" in stream.getvalue()


def test_emitter_survives_an_unwritable_path(tmp_path):
    """Если файл не открылся, поток всё равно обязан получить отчёт: ремень,
    который молчит из-за прав на каталог, хуже отсутствующего."""
    import io

    from app.services.suite_ram_belt import make_emitter

    stream = io.StringIO()
    emit = make_emitter(tmp_path / "нет-такого-каталога" / "belt.log", stream)

    emit("ТЕЧЬ: tests/x.py::test_y")

    assert "tests/x.py::test_y" in stream.getvalue()
