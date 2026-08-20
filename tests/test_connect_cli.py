# -*- coding: utf-8 -*-
"""Сторожа единственного входа подключения: ``chatter.connect.__main__``.

Пишутся ОТ СПЕКИ ``docs/superpowers/specs/2026-08-20-t7-turnkey-connect.md``
автором, который планового кода не видел ([[jarvis-guards-not-by-the-plan-author]]).
Предмет — ровно CLI-слой: §4 (один вход, флаги, четыре кода выхода, формат
остановки), §12.3 (единственный порядок исполнения), §12.4 (три строки),
§12.6 п.3–5, §12.7 п.1 и п.3.

Пробы и действия здесь ПОДСТАВНЫЕ. Это осознанно: механику фактов на диске
держат другие сторожа, а тут проверяется то, чего кроме ``__main__`` никто не
делает — различимость четырёх кодов, ветка «ждёт человека» у автошага, форма
остановки, отсутствие трассировки при нарушении контракта, безвредность
``--plan`` и одноразовость ``--drill-yes``.

Живых подпроцессов нет ни одного: наружу код ходит только через
``CommandRunner`` (§12.2), и сторож подставляет свой — по форме, а не по
родству (§12.7 п.7).
"""
from __future__ import annotations

import hashlib
import importlib
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pytest


# --------------------------------------------------------------------------
# импорт контракта (§12.1, §12.2)
# --------------------------------------------------------------------------

model = importlib.import_module("chatter.connect.model")
cli = importlib.import_module("chatter.connect.__main__")

Verdict = model.Verdict
Owner = model.Owner
Step = model.Step
StepResult = model.StepResult
CommandResult = model.CommandResult


STOP_PREFIX = "ОСТАНОВ на "
WHY_PREFIX = "ПОЧЕМУ: "
TODO_PREFIX = "ЧТО СДЕЛАТЬ: "
REPEAT_TAIL = "повтори ту же команду"

SLUG = "acme"


def _contract_error() -> type:
    """``ConnectContractError`` — §12.6 п.5. Модуль контрактом не назван."""
    for modname in (
        "chatter.connect.model",
        "chatter.connect",
        "chatter.connect.steps",
        "chatter.connect.__main__",
    ):
        mod = sys.modules.get(modname) or importlib.import_module(modname)
        exc = getattr(mod, "ConnectContractError", None)
        if exc is not None:
            return exc
    pytest.fail(
        "§12.6 п.5: ConnectContractError не найден ни в model.py, ни в "
        "__init__.py, ни в steps.py, ни в __main__.py"
    )


# --------------------------------------------------------------------------
# подставной раннер (§12.2: run(argv, *, cwd, timeout) -> CommandResult)
# --------------------------------------------------------------------------


@dataclass
class Call:
    argv: list
    cwd: Any
    timeout: Any


@dataclass
class RecordingRunner:
    """Единственный путь наружу. Ничего не запускает, всё записывает."""

    result: Any = None
    raises: BaseException | None = None
    calls: list = field(default_factory=list)

    def run(self, argv, *, cwd, timeout):
        self.calls.append(Call(list(argv), cwd, timeout))
        if self.raises is not None:
            raise self.raises
        if self.result is not None:
            return self.result
        return CommandResult(rc=0, stdout="", stderr="")


# --------------------------------------------------------------------------
# подставные шаги
# --------------------------------------------------------------------------


def _result(
    step_id: str,
    verdict,
    *,
    waits: bool = False,
    why: str | None = None,
    todo: str | None = None,
    facts: dict | None = None,
) -> Any:
    """``StepResult`` по §12.2.

    ``waits_for_human`` (§12.7 п.1) передаётся ТОЛЬКО когда он нужен: иначе
    отсутствие поля красило бы красным все тесты подряд вместо тех двух, что
    на него и заведены.
    """
    kwargs = dict(
        step_id=step_id,
        verdict=verdict,
        why=why if why is not None else f"факта {step_id} нет на диске",
        todo=todo if todo is not None else f"закрой {step_id} руками",
        facts=facts if facts is not None else {},
    )
    if waits:
        kwargs["waits_for_human"] = True
    return StepResult(**kwargs)


class FakeStep:
    """Шаг с заданным сценарием проб.

    ``verdicts`` — последовательность вердиктов проб; последний «липнет».
    Элемент — либо ``Verdict``, либо кортеж ``(Verdict, waits_for_human)``.
    """

    def __init__(
        self,
        step_id: str,
        owner,
        verdicts,
        *,
        title: str | None = None,
        act_returns=None,
        act_effect: Callable[[Any], None] | None = None,
        why: str | None = None,
        todo: str | None = None,
    ):
        self.id = step_id
        self.owner = owner
        self.title = title if title is not None else f"заголовок {step_id}"
        self._verdicts = list(verdicts)
        self._act_returns = act_returns
        self._act_effect = act_effect
        self._why = why
        self._todo = todo
        self.probe_calls: list = []
        self.act_calls: list = []

    # -- пробы / действия --------------------------------------------------

    def probe(self, ctx):
        idx = min(len(self.probe_calls), len(self._verdicts) - 1)
        self.probe_calls.append(ctx)
        item = self._verdicts[idx]
        verdict, waits = item if isinstance(item, tuple) else (item, False)
        return _result(
            self.id, verdict, waits=waits, why=self._why, todo=self._todo
        )

    def act(self, ctx):
        self.act_calls.append(ctx)
        if self._act_effect is not None:
            self._act_effect(ctx)
        if self._act_returns is not None:
            spec = self._act_returns
            verdict, waits = spec if isinstance(spec, tuple) else (spec, False)
            return _result(
                self.id, verdict, waits=waits, why=self._why, todo=self._todo
            )
        return _result(self.id, Verdict.OPEN, why=self._why, todo=self._todo)

    # -- сборка -------------------------------------------------------------

    def as_step(self):
        return Step(
            id=self.id,
            title=self.title,
            owner=self.owner,
            probe=self.probe,
            act=self.act if self.owner is Owner.AUTO else None,
        )

    @property
    def probes(self) -> int:
        return len(self.probe_calls)

    @property
    def acts(self) -> int:
        return len(self.act_calls)


def steps_of(*fakes) -> tuple:
    return tuple(f.as_step() for f in fakes)


_BUILDERS = ("build_steps", "make_steps", "all_steps", "steps_for", "get_steps")


def patch_steps(monkeypatch, steps) -> None:
    """Подменяет литерал ``STEPS`` (§12.1) во всех местах, где он виден.

    Контракт не говорит, импортирует ли ``__main__`` имя или модуль, поэтому
    патчатся оба варианта. ``steps`` может быть и объектом, который бросает
    на итерации — так проверяется §12.6 п.5.
    """
    patched = 0
    for modname in ("chatter.connect.steps", "chatter.connect.__main__"):
        mod = sys.modules.get(modname) or importlib.import_module(modname)
        if hasattr(mod, "STEPS"):
            monkeypatch.setattr(mod, "STEPS", steps)
            patched += 1
        for name in _BUILDERS:
            if callable(getattr(mod, name, None)):
                monkeypatch.setattr(mod, name, lambda *a, **k: steps)
                patched += 1
    assert patched, (
        "§12.1: ни STEPS, ни функция сборки шагов не видны из chatter.connect.steps "
        "/ chatter.connect.__main__ — сторож не может подставить сценарий"
    )


# --------------------------------------------------------------------------
# вызов CLI (§12.7 п.3)
# --------------------------------------------------------------------------


def run_main(argv, *, runner, root) -> int:
    """Возвращает код выхода, чем бы main его ни отдал."""
    try:
        rc = cli.main(list(argv), runner=runner, root=Path(root))
    except SystemExit as exc:  # argparse и прочие ранние выходы
        code = exc.code
        if code is None:
            return 0
        assert isinstance(code, int), f"код выхода обязан быть int, получено {code!r}"
        return code
    assert isinstance(rc, int), (
        f"§12.7 п.3: main(...) -> int, получено {type(rc).__name__} ({rc!r})"
    )
    return rc


@dataclass
class Stop:
    stop_line: str
    why: str
    todo: str
    first_index: int
    lines: list


def parse_stop(text: str) -> Stop:
    """Разбирает блок остановки §12.4 структурно, а не по буквам сообщения."""
    lines = [ln.rstrip() for ln in text.splitlines()]
    found: dict = {}
    for i, line in enumerate(lines):
        stripped = line.strip()
        for key, prefix in (
            ("stop", STOP_PREFIX),
            ("why", WHY_PREFIX),
            ("todo", TODO_PREFIX),
        ):
            if stripped.startswith(prefix):
                assert key not in found, (
                    f"§12.4: префикс {prefix!r} встречается больше одного раза:\n{text}"
                )
                found[key] = i
    for key, prefix in (
        ("stop", STOP_PREFIX),
        ("why", WHY_PREFIX),
        ("todo", TODO_PREFIX),
    ):
        assert key in found, f"§12.4: в выводе нет строки {prefix!r}:\n{text}"
    assert found["stop"] < found["why"] < found["todo"], (
        f"§12.4: порядок строк нарушен ({found}):\n{text}"
    )

    def _join(lo: int, hi: int | None) -> str:
        chunk = lines[lo:hi] if hi is not None else lines[lo:]
        return " ".join(part.strip() for part in chunk if part.strip())

    why = _join(found["why"], found["todo"])[len(WHY_PREFIX):].strip()
    todo = _join(found["todo"], None)[len(TODO_PREFIX):].strip()
    return Stop(
        stop_line=lines[found["stop"]].strip(),
        why=why,
        todo=todo,
        first_index=found["stop"],
        lines=lines,
    )


def out_of(capsys) -> str:
    captured = capsys.readouterr()
    return captured.out + captured.err


def assert_no_traceback(text: str) -> None:
    for marker in ("Traceback (most recent call last)", 'File "', "\n  File "):
        assert marker not in text, (
            f"человеку, пришедшему подключать клиента, показали трассировку:\n{text}"
        )


def snapshot(root: Path) -> dict:
    out: dict = {}
    for path in sorted(Path(root).rglob("*")):
        if path.is_file():
            out[str(path.relative_to(root))] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return out


def journal_path(root: Path, slug: str = SLUG) -> Path:
    return Path(root) / "state" / "connect" / f"{slug}.md"


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    return r


@pytest.fixture()
def runner() -> RecordingRunner:
    return RecordingRunner()


# ==========================================================================
# 1. Четыре кода выхода и их различимость (§4)
# ==========================================================================


def test_all_steps_closed_gives_ready_code_0(monkeypatch, root, runner, capsys):
    """Все шаги закрыты -> 0 ГОТОВО, и ни одной остановки в выводе."""
    a = FakeStep("S4", Owner.AUTO, [Verdict.CLOSED])
    h = FakeStep("S5", Owner.HUMAN, [Verdict.CLOSED])
    patch_steps(monkeypatch, steps_of(a, h))

    rc = run_main([SLUG], runner=runner, root=root)
    text = out_of(capsys)

    assert rc == 0, f"все факты на диске -> код 0, получено {rc}"
    assert STOP_PREFIX not in text, f"при готовом подключении печатать останов нечего:\n{text}"
    assert a.acts == 0, "закрытый шаг не имеет права выполняться повторно (§1 идемпотентность)"


def test_open_human_step_gives_waiting_code_3_never_1(monkeypatch, root, runner, capsys):
    """Штатная остановка на человеческом шаге — это 3, и НИКОГДА не 1 (§4, q2)."""
    a = FakeStep("S4", Owner.AUTO, [Verdict.CLOSED])
    h = FakeStep("S6", Owner.HUMAN, [Verdict.OPEN])
    tail = FakeStep("S9", Owner.AUTO, [Verdict.OPEN], act_returns=Verdict.CLOSED)
    patch_steps(monkeypatch, steps_of(a, h, tail))

    rc = run_main([SLUG], runner=runner, root=root)
    text = out_of(capsys)

    assert rc == 3, (
        f"«жду тебя» обязано быть кодом 3, получено {rc}; слипшееся со «сломано» "
        f"приучает не смотреть на красное (решение владельца q2)"
    )
    stop = parse_stop(text)
    assert stop.stop_line.startswith(f"{STOP_PREFIX}S6:")


def test_probe_conflict_gives_broken_code_1(monkeypatch, root, runner, capsys):
    """ПРОТИВОРЕЧИЕ — дефект, а не ожидание: код 1 (§1, §12.3)."""
    a = FakeStep("S4", Owner.AUTO, [Verdict.CONFLICT])
    patch_steps(monkeypatch, steps_of(a))

    rc = run_main([SLUG], runner=runner, root=root)
    text = out_of(capsys)

    assert rc == 1, f"противоречие фактов -> код 1, получено {rc}"
    assert a.acts == 0, "§12.3: при CONFLICT действие не выполняется вовсе"
    parse_stop(text)


def test_waiting_and_broken_are_different_codes(monkeypatch, root, runner, capsys):
    """Один и тот же шаг: ожидание и противоречие обязаны РАЗЛИЧАТЬСЯ."""
    waiting = FakeStep("S7", Owner.HUMAN, [Verdict.OPEN])
    patch_steps(monkeypatch, steps_of(waiting))
    rc_waiting = run_main([SLUG], runner=runner, root=root)
    out_of(capsys)

    broken = FakeStep("S7", Owner.HUMAN, [Verdict.CONFLICT])
    patch_steps(monkeypatch, steps_of(broken))
    rc_broken = run_main([SLUG], runner=runner, root=root)
    out_of(capsys)

    assert rc_waiting != rc_broken, (
        "«жду тебя» и «сломано» слиплись в один код — ровно то, чего запретил "
        "владелец решением q2"
    )
    assert {rc_waiting, rc_broken} == {3, 1}


def test_four_exit_codes_are_all_reachable_and_pairwise_distinct(
    monkeypatch, root, runner, capsys
):
    """0 · 3 · 1 · 2 — четыре достижимых и попарно разных исхода (§4)."""
    codes = {}

    patch_steps(monkeypatch, steps_of(FakeStep("S4", Owner.AUTO, [Verdict.CLOSED])))
    codes["ready"] = run_main([SLUG], runner=runner, root=root)
    out_of(capsys)

    patch_steps(monkeypatch, steps_of(FakeStep("S5", Owner.HUMAN, [Verdict.OPEN])))
    codes["waits"] = run_main([SLUG], runner=runner, root=root)
    out_of(capsys)

    patch_steps(monkeypatch, steps_of(FakeStep("S5", Owner.HUMAN, [Verdict.CONFLICT])))
    codes["conflict"] = run_main([SLUG], runner=runner, root=root)
    out_of(capsys)

    broken = _Exploding(_contract_error()("шаг S9 объявлен AUTO, но act отсутствует"))
    patch_steps(monkeypatch, broken)
    codes["not_run"] = run_main([SLUG], runner=runner, root=root)
    out_of(capsys)

    assert codes == {"ready": 0, "waits": 3, "conflict": 1, "not_run": 2}, codes
    assert len(set(codes.values())) == 4, f"коды выхода слиплись: {codes}"


# ==========================================================================
# 2. Ветка «ждёт человека» у автошага (§12.7 п.1) — денежные ворота S13
# ==========================================================================


def test_auto_step_waiting_for_human_from_act_gives_3(monkeypatch, root, runner, capsys):
    """act вернул waits_for_human=True (S13 без --drill-yes) -> 3, а не 1."""
    money = FakeStep(
        "S13",
        Owner.AUTO,
        [Verdict.OPEN],
        act_returns=(Verdict.OPEN, True),
        why="дрил спишет ~$0.29, разрешения нет",
        todo="повтори с --drill-yes",
    )
    patch_steps(monkeypatch, steps_of(money))

    rc = run_main([SLUG], runner=runner, root=root)
    text = out_of(capsys)

    assert rc == 3, (
        f"денежные ворота S13 — штатное ожидание человека (§5.8), обязаны давать 3, "
        f"получено {rc}: на штатном пути напечатано «сломано»"
    )
    stop = parse_stop(text)
    assert stop.stop_line.startswith(f"{STOP_PREFIX}S13:")


def test_auto_step_waiting_for_human_from_reprobe_gives_3(monkeypatch, root, runner, capsys):
    """Повторная проба вернула waits_for_human=True -> тоже 3 (§12.7 п.1)."""
    money = FakeStep(
        "S13",
        Owner.AUTO,
        [Verdict.OPEN, (Verdict.OPEN, True)],
        act_returns=Verdict.OPEN,
    )
    patch_steps(monkeypatch, steps_of(money))

    rc = run_main([SLUG], runner=runner, root=root)
    out_of(capsys)

    assert rc == 3, f"waits_for_human у повторной пробы обязан давать 3, получено {rc}"


def test_auto_step_failure_without_waiting_flag_gives_1(monkeypatch, root, runner, capsys):
    """Действие отработало, факт не появился, ожидания нет -> 1 (§12.3)."""
    step = FakeStep("S10", Owner.AUTO, [Verdict.OPEN], act_returns=Verdict.OPEN)
    patch_steps(monkeypatch, steps_of(step))

    rc = run_main([SLUG], runner=runner, root=root)
    out_of(capsys)

    assert rc == 1, f"провал автошага без ожидания человека -> 1, получено {rc}"
    assert step.acts == 1


def test_closed_verdict_wins_over_waiting_flag(monkeypatch, root, runner, capsys):
    """CLOSED + waits_for_human=True -> идём дальше (уточнение §12.7).

    Факт на диске старше ожидания: если шаг закрыт, булево поле не
    останавливает прогон.
    """
    closed_but_flagged = FakeStep("S13", Owner.AUTO, [(Verdict.CLOSED, True)])
    following = FakeStep("S14", Owner.HUMAN, [Verdict.CLOSED])
    patch_steps(monkeypatch, steps_of(closed_but_flagged, following))

    rc = run_main([SLUG], runner=runner, root=root)
    text = out_of(capsys)

    assert rc == 0, (
        f"закрытый шаг обязан пропускаться, что бы ни стояло в waits_for_human; "
        f"получено {rc}"
    )
    assert following.probes >= 1, "прогон остановился на ЗАКРЫТОМ шаге"
    assert STOP_PREFIX not in text


# ==========================================================================
# 3. Три строки остановки (§12.4) — структура, не буквы
# ==========================================================================


@pytest.mark.parametrize(
    "name, fakes, expected_rc, stop_id",
    [
        (
            "человеческий шаг",
            lambda: (FakeStep("S6", Owner.HUMAN, [Verdict.OPEN], title="логин Telegram"),),
            3,
            "S6",
        ),
        (
            "противоречие фактов",
            lambda: (FakeStep("S10", Owner.AUTO, [Verdict.CONFLICT], title="запись реестра"),),
            1,
            "S10",
        ),
        (
            "автошаг не доказал факт",
            lambda: (
                FakeStep(
                    "S11",
                    Owner.AUTO,
                    [Verdict.OPEN],
                    title="подъём раннера",
                    act_returns=Verdict.OPEN,
                ),
            ),
            1,
            "S11",
        ),
        (
            "автошаг ждёт разрешения",
            lambda: (
                FakeStep(
                    "S13",
                    Owner.AUTO,
                    [Verdict.OPEN],
                    title="первый дрил",
                    act_returns=(Verdict.OPEN, True),
                ),
            ),
            3,
            "S13",
        ),
    ],
)
def test_every_stop_prints_three_lines_in_order(
    monkeypatch, root, runner, capsys, name, fakes, expected_rc, stop_id
):
    """У КАЖДОЙ ветки остановки — три префикса в заданном порядке (§12.4, Д11)."""
    built = fakes()
    fake = built[0]
    patch_steps(monkeypatch, steps_of(*built))

    rc = run_main([SLUG], runner=runner, root=root)
    text = out_of(capsys)

    assert rc == expected_rc, f"{name}: код {rc}"
    stop = parse_stop(text)
    assert stop.stop_line.startswith(f"{STOP_PREFIX}{stop_id}: "), stop.stop_line
    assert fake.title in stop.stop_line, (
        f"§12.4: первая строка обязана назвать заголовок шага: {stop.stop_line!r}"
    )
    assert stop.why, "§12.4: ПОЧЕМУ пусто"
    assert stop.todo, "§12.4/Д11: ЧТО СДЕЛАТЬ пусто — остановка без адреса действия"


def test_stop_carries_why_and_todo_of_the_step(monkeypatch, root, runner, capsys):
    """Текст берётся из StepResult, а не сочиняется CLI (§12.2)."""
    step = FakeStep(
        "S7",
        Owner.HUMAN,
        [Verdict.OPEN],
        title="токен контрол-бота",
        why="переменная токена пуста",
        todo="возьми токен у BotFather",
    )
    patch_steps(monkeypatch, steps_of(step))

    run_main([SLUG], runner=runner, root=root)
    stop = parse_stop(out_of(capsys))

    assert "переменная токена пуста" in stop.why
    assert "возьми токен у BotFather" in stop.todo


def test_todo_line_always_ends_with_repeat_the_same_command(monkeypatch, root, runner, capsys):
    """Концовка «после этого повтори ту же команду» обязательна ВЕЗДЕ (§4)."""
    branches = [
        (FakeStep("S5", Owner.HUMAN, [Verdict.OPEN]), 3),
        (FakeStep("S9", Owner.AUTO, [Verdict.CONFLICT]), 1),
        (FakeStep("S10", Owner.AUTO, [Verdict.OPEN], act_returns=Verdict.OPEN), 1),
        (FakeStep("S13", Owner.AUTO, [Verdict.OPEN], act_returns=(Verdict.OPEN, True)), 3),
    ]
    for fake, expected_rc in branches:
        patch_steps(monkeypatch, steps_of(fake))
        rc = run_main([SLUG], runner=runner, root=root)
        stop = parse_stop(out_of(capsys))
        assert rc == expected_rc, fake.id
        tail = stop.todo.rstrip().rstrip(".").rstrip()
        assert tail.endswith(REPEAT_TAIL), (
            f"§4: остановка на {fake.id} не сказала, чем продолжать — "
            f"вход один, и человек не должен это помнить: {stop.todo!r}"
        )


def test_stop_block_prints_nothing_beyond_three_lines(monkeypatch, root, runner, capsys):
    """«ТРИ строки и ничего сверх» (§4): после ОСТАНОВа лишнего вывода нет."""
    step = FakeStep("S6", Owner.HUMAN, [Verdict.OPEN], why="сессии нет", todo="войди в аккаунт")
    patch_steps(monkeypatch, steps_of(step))

    run_main([SLUG], runner=runner, root=root)
    text = out_of(capsys)
    stop = parse_stop(text)

    tail = [ln for ln in stop.lines[stop.first_index:] if ln.strip()]
    assert len(tail) == 3, (
        "§4: блок остановки обязан быть ровно из трёх строк, получено "
        f"{len(tail)}:\n" + "\n".join(tail)
    )


def test_stop_happens_once_and_nothing_runs_after_it(monkeypatch, root, runner, capsys):
    """Остановка — это выход, а не пометка: следующие шаги не трогаются (§12.3)."""
    first = FakeStep("S5", Owner.HUMAN, [Verdict.OPEN])
    later = FakeStep("S9", Owner.AUTO, [Verdict.OPEN], act_returns=Verdict.CLOSED)
    latest = FakeStep("S13", Owner.AUTO, [Verdict.OPEN], act_returns=Verdict.CLOSED)
    patch_steps(monkeypatch, steps_of(first, later, latest))

    rc = run_main([SLUG], runner=runner, root=root)
    text = out_of(capsys)

    assert rc == 3
    assert text.count(STOP_PREFIX) == 1, f"остановка напечатана не один раз:\n{text}"
    assert later.probes == 0 and later.acts == 0, "после остановки прогон продолжился"
    assert latest.acts == 0, "после остановки выполнен ПЛАТНЫЙ шаг"


# ==========================================================================
# 4. Порядок исполнения §12.3: доказательство, а не «act не упал»
# ==========================================================================


def test_probe_runs_again_after_act(monkeypatch, root, runner, capsys):
    """После действия проба обязательна: «команда отработала» ≠ «факт появился»."""
    step = FakeStep("S10", Owner.AUTO, [Verdict.OPEN, Verdict.CLOSED], act_returns=Verdict.CLOSED)
    patch_steps(monkeypatch, steps_of(step))

    rc = run_main([SLUG], runner=runner, root=root)
    out_of(capsys)

    assert rc == 0
    assert step.acts == 1
    assert step.probes == 2, (
        f"§12.3: после act обязана быть ПОВТОРНАЯ проба, проб было {step.probes}"
    )


def test_act_returning_closed_does_not_override_open_probe(monkeypatch, root, runner, capsys):
    """act сказал «закрыто», проба говорит «нет» — верим пробе (§12.3)."""
    step = FakeStep("S11", Owner.AUTO, [Verdict.OPEN], act_returns=Verdict.CLOSED)
    patch_steps(monkeypatch, steps_of(step))

    rc = run_main([SLUG], runner=runner, root=root)
    out_of(capsys)

    assert rc == 1, (
        f"код поверил отчёту действия вместо факта на диске: получено {rc}"
    )


def test_human_step_never_gets_acted(monkeypatch, root, runner, capsys):
    """AUTO ⇔ act (§3). У HUMAN-шага действия нет, и CLI его не выдумывает."""
    human = FakeStep("S6", Owner.HUMAN, [Verdict.OPEN])
    patch_steps(monkeypatch, steps_of(human))

    run_main([SLUG], runner=runner, root=root)
    out_of(capsys)

    assert human.acts == 0
    assert runner.calls == [], "на человеческом шаге команда ходила наружу"


def test_steps_are_probed_in_declared_order(monkeypatch, root, runner, capsys):
    """Порядок выбирает код, а не человек: STEPS исполняется как объявлен (§4)."""
    order: list = []
    first = FakeStep("S4", Owner.AUTO, [Verdict.CLOSED])
    second = FakeStep("S9", Owner.AUTO, [Verdict.CLOSED])
    third = FakeStep("S10", Owner.HUMAN, [Verdict.OPEN])
    for fake in (first, second, third):
        original = fake.probe

        def probed(ctx, _fake=fake, _orig=original):
            order.append(_fake.id)
            return _orig(ctx)

        fake.probe = probed  # type: ignore[method-assign]
    patch_steps(monkeypatch, steps_of(first, second, third))

    run_main([SLUG], runner=runner, root=root)
    out_of(capsys)

    assert order == ["S4", "S9", "S10"], order


# ==========================================================================
# 5. Нарушение контракта = код 2, а не трассировка (§12.6 п.5)
# ==========================================================================


class _Exploding:
    """Объект вместо STEPS: сборка шагов падает при обходе."""

    def __init__(self, exc: BaseException):
        self._exc = exc

    def __iter__(self):
        raise self._exc

    def __len__(self):
        raise self._exc


def test_contract_error_gives_code_2_without_traceback(monkeypatch, root, runner, capsys):
    """ConnectContractError -> «прогон НЕ состоялся», код 2, внятная строка."""
    detail = "шаг S9 объявлен AUTO, но act отсутствует"
    patch_steps(monkeypatch, _Exploding(_contract_error()(detail)))

    rc = run_main([SLUG], runner=runner, root=root)
    text = out_of(capsys)

    assert rc == 2, f"нарушение контракта -> код 2, получено {rc}"
    assert_no_traceback(text)
    assert text.strip(), "прогон не состоялся молча — человеку не сказали ничего"
    assert detail in text, (
        f"строка не называет, ЧТО именно нарушено (§12.6 п.5):\n{text}"
    )
    assert "контракт" in text.lower(), (
        f"§12.6 п.5: сообщение обязано назвать нарушение контракта:\n{text}"
    )


def test_any_assembly_exception_gives_code_2_without_traceback(
    monkeypatch, root, runner, capsys
):
    """«и любое исключение сборки шагов» — не только своё (§12.6 п.5)."""
    patch_steps(monkeypatch, _Exploding(RuntimeError("STEPS собрать не удалось: дубль id S9")))

    rc = run_main([SLUG], runner=runner, root=root)
    text = out_of(capsys)

    assert rc == 2, f"исключение сборки шагов -> код 2, получено {rc}"
    assert_no_traceback(text)
    assert text.strip()


def test_bad_arguments_give_code_2(root, runner, capsys):
    """«не прочитали вход» — это 2 (§4): слаг не назван / флаг неизвестен."""
    rc_missing = run_main([], runner=runner, root=root)
    out_of(capsys)
    rc_unknown = run_main([SLUG, "--открой-трафик"], runner=runner, root=root)
    out_of(capsys)

    assert rc_missing == 2, f"без слага прогон ничего не доказал -> 2, получено {rc_missing}"
    assert rc_unknown == 2, f"неизвестный флаг -> 2, получено {rc_unknown}"


def test_runner_failure_escaping_a_step_is_never_success(monkeypatch, root, capsys):
    """Раннер взорвался и шаг это не поймал — прогон НЕ «готово» и не трассировка.

    «Замер не состоялся» и «замер сказал нет» обязаны различаться (§12.6 п.3):
    код 0 или 3 здесь означал бы, что мы выдали несостоявшийся прогон за
    штатный исход.
    """
    exploding = RecordingRunner(raises=subprocess.TimeoutExpired(cmd=["pwsh"], timeout=1.0))

    def call_runner(ctx):
        ctx.runner.run(["pwsh", "-File", "scripts/chatter_client.ps1"], cwd=Path(root), timeout=1.0)

    step = FakeStep("S11", Owner.AUTO, [Verdict.OPEN], act_effect=call_runner)
    patch_steps(monkeypatch, steps_of(step))

    rc = run_main([SLUG], runner=exploding, root=root)
    text = out_of(capsys)

    assert rc in (1, 2), (
        f"невозможность выполнить внешнюю команду выдана за штатный исход: код {rc}"
    )
    assert_no_traceback(text)
    assert text.strip(), "прогон умер молча"


def test_runner_exception_and_runner_refusal_are_distinguishable(
    monkeypatch, root, capsys
):
    """«Замер не состоялся» и «замер сказал нет» — разные сообщения (§12.6 п.3)."""

    def act_reading_runner(ctx):
        result = ctx.runner.run(["pwsh", "-File", "scripts/chatter_client.ps1"], cwd=Path(root), timeout=1.0)
        return result

    refusing = RecordingRunner(result=CommandResult(rc=1, stdout="", stderr="радиус не нулевой"))

    def act_refused(ctx):
        act_reading_runner(ctx)

    refused_step = FakeStep(
        "S11",
        Owner.AUTO,
        [Verdict.OPEN],
        act_effect=act_refused,
        why="замер радиуса вернул отказ",
    )
    patch_steps(monkeypatch, steps_of(refused_step))
    rc_refused = run_main([SLUG], runner=refusing, root=root)
    text_refused = out_of(capsys)

    broken_step = FakeStep(
        "S11",
        Owner.AUTO,
        [Verdict.CONFLICT],
        why="замер не состоялся: исполняемый не найден",
    )
    patch_steps(monkeypatch, steps_of(broken_step))
    rc_broken = run_main([SLUG], runner=RecordingRunner(raises=FileNotFoundError("pwsh")), root=root)
    text_broken = out_of(capsys)

    assert rc_refused in (1, 2, 3) and rc_broken in (1, 2, 3)
    assert parse_stop(text_refused).why != parse_stop(text_broken).why, (
        "«замер сказал нет» и «замер не состоялся» напечатаны одинаково"
    )


# ==========================================================================
# 6. --plan не выполняет ни одного действия (§4)
# ==========================================================================


def test_plan_touches_neither_runner_nor_disk(monkeypatch, root, runner, capsys):
    """--plan: ни одного вызова наружу и ни одной записи на диск."""

    def writes(ctx):
        (Path(root) / "act_happened.txt").write_text("нет", encoding="utf-8")
        ctx.runner.run(["pwsh", "-File", "scripts/chatter_client.ps1"], cwd=Path(root), timeout=5.0)

    done = FakeStep("S4", Owner.AUTO, [Verdict.CLOSED])
    pending = FakeStep("S10", Owner.AUTO, [Verdict.OPEN], act_effect=writes, act_returns=Verdict.CLOSED)
    human = FakeStep("S13", Owner.HUMAN, [Verdict.OPEN])
    patch_steps(monkeypatch, steps_of(done, pending, human))

    before = snapshot(root)
    rc = run_main([SLUG, "--plan"], runner=runner, root=root)
    text = out_of(capsys)
    after = snapshot(root)

    assert runner.calls == [], f"--plan сходил наружу: {runner.calls}"
    assert pending.acts == 0, "--plan выполнил действие незакрытого шага"
    assert before == after, (
        "--plan изменил дерево: "
        f"добавлено {sorted(set(after) - set(before))}, "
        f"изменено {sorted(k for k in set(after) & set(before) if after[k] != before[k])}"
    )
    assert not journal_path(root).exists(), "--plan завёл журнал, хотя ничего не делал"
    assert rc in (0, 1, 2, 3), rc
    assert_no_traceback(text)


def test_plan_prints_the_whole_map_with_verdicts(monkeypatch, root, runner, capsys):
    """--plan печатает карту шагов и вердикты — всех, а не до первой остановки.

    Как именно нарисован вердикт (слово, значок), сторож не судит: он требует
    ровно того, чтобы три РАЗНЫХ вердикта нельзя было спутать между собой.
    Владелец и HUMAN/AUTO у всех трёх шагов одинаковые — значит разница в
    строке может идти только от вердикта.
    """
    closed = FakeStep("S9", Owner.AUTO, [Verdict.CLOSED], title="одинаковый заголовок")
    opened = FakeStep("S10", Owner.AUTO, [Verdict.OPEN], title="одинаковый заголовок")
    broken = FakeStep("S11", Owner.AUTO, [Verdict.CONFLICT], title="одинаковый заголовок")
    patch_steps(monkeypatch, steps_of(closed, opened, broken))

    run_main([SLUG, "--plan"], runner=runner, root=root)
    text = out_of(capsys)

    rendered = {}
    for fake in (closed, opened, broken):
        assert fake.probes >= 1, f"--plan не спросил вердикт у {fake.id}"
        line = next((ln for ln in text.splitlines() if fake.id in ln), None)
        assert line is not None, f"--plan не назвал шаг {fake.id}:\n{text}"
        rendered[fake.id] = line.replace(fake.id, "").replace(fake.title, "").strip()

    assert len(set(rendered.values())) == 3, (
        "--plan нарисовал разные вердикты одинаково — карта без вердиктов "
        f"(§4): {rendered}"
    )


def test_plan_does_not_act_even_on_a_money_step(monkeypatch, root, runner, capsys):
    """--plan вместе с --drill-yes всё равно не тратит денег (§4)."""
    money = FakeStep("S13", Owner.AUTO, [Verdict.OPEN], act_returns=Verdict.CLOSED)
    patch_steps(monkeypatch, steps_of(money))

    before = snapshot(root)
    run_main([SLUG, "--plan", "--drill-yes"], runner=runner, root=root)
    out_of(capsys)

    assert money.acts == 0, "--plan + --drill-yes выполнил платный шаг"
    assert runner.calls == []
    assert snapshot(root) == before


# ==========================================================================
# 7. Журнал: пишет только __main__ и не читается никогда (§1, §12.6 п.4)
# ==========================================================================


def test_journal_is_written_by_main(monkeypatch, root, runner, capsys):
    """state/connect/<slug>.md заводит именно CLI (§12.6 п.4)."""
    step = FakeStep("S4", Owner.AUTO, [Verdict.OPEN, Verdict.CLOSED], act_returns=Verdict.CLOSED)
    human = FakeStep("S5", Owner.HUMAN, [Verdict.OPEN])
    patch_steps(monkeypatch, steps_of(step, human))

    rc = run_main([SLUG], runner=runner, root=root)
    out_of(capsys)

    assert rc == 3
    path = journal_path(root)
    assert path.exists(), f"§1: журнал {path} не заведён"
    body = path.read_text(encoding="utf-8")
    assert body.strip(), "журнал пуст"
    assert "S4" in body, f"журнал не назвал исполненный шаг:\n{body}"


def test_deleting_the_journal_changes_no_verdict(monkeypatch, root, runner, capsys):
    """Журнал удалён — вердикты и код выхода те же (Д7, направление первое)."""
    def build():
        return steps_of(
            FakeStep("S4", Owner.AUTO, [Verdict.CLOSED]),
            FakeStep("S6", Owner.HUMAN, [Verdict.OPEN]),
        )

    patch_steps(monkeypatch, build())
    rc_first = run_main([SLUG], runner=runner, root=root)
    first_stop = parse_stop(out_of(capsys)).stop_line

    path = journal_path(root)
    if path.exists():
        path.unlink()

    patch_steps(monkeypatch, build())
    rc_second = run_main([SLUG], runner=runner, root=root)
    second_stop = parse_stop(out_of(capsys)).stop_line

    assert rc_first == rc_second == 3
    assert first_stop == second_stop


def test_faked_journal_changes_neither_verdicts_nor_exit_code(monkeypatch, root, runner, capsys):
    """Подделанный журнал «всё готово» не двигает ни один вердикт (Д7, второе)."""
    clean = Path(root) / "clean"
    faked = Path(root) / "faked"
    for base in (clean, faked):
        base.mkdir()
    journal = journal_path(faked)
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text(
        "# подключение acme\n"
        "S0 закрыт\nS4 закрыт\nS10 закрыт\nS11 закрыт\nS12 закрыт\nS13 закрыт\n"
        "ГОТОВО: подключение завершено, осталось открыть трафик\n",
        encoding="utf-8",
    )

    def build():
        return (
            FakeStep("S4", Owner.AUTO, [Verdict.CLOSED]),
            FakeStep("S6", Owner.HUMAN, [Verdict.OPEN]),
            FakeStep("S13", Owner.AUTO, [Verdict.OPEN], act_returns=Verdict.CLOSED),
        )

    clean_steps = build()
    patch_steps(monkeypatch, steps_of(*clean_steps))
    rc_clean = run_main([SLUG], runner=runner, root=clean)
    stop_clean = parse_stop(out_of(capsys)).stop_line

    faked_steps = build()
    faked_runner = RecordingRunner()
    patch_steps(monkeypatch, steps_of(*faked_steps))
    rc_faked = run_main([SLUG], runner=faked_runner, root=faked)
    stop_faked = parse_stop(out_of(capsys)).stop_line

    assert rc_faked == rc_clean == 3, (rc_clean, rc_faked)
    assert stop_faked == stop_clean, (
        "подделанный журнал сдвинул остановку — состояние читается не только из фактов"
    )
    assert [f.probes for f in faked_steps] == [f.probes for f in clean_steps], (
        "с подделанным журналом пробы вызывались иначе: журнал прочитан как состояние"
    )
    assert faked_steps[2].acts == 0, "подделка журнала изменила поведение платного шага"


# ==========================================================================
# 8. Флаги: одноразовость --drill-yes (§4, Д17) и доставка в Ctx
# ==========================================================================


def test_flags_reach_the_context(monkeypatch, root, runner, capsys):
    """--drill-yes / --drill-again доезжают до Ctx, а не остаются в argparse."""
    seen: list = []

    def record(ctx):
        seen.append((bool(ctx.drill_yes), bool(ctx.drill_again)))
        return _result("S13", Verdict.OPEN)

    step = FakeStep("S13", Owner.HUMAN, [Verdict.OPEN])
    step.probe = record  # type: ignore[method-assign]
    patch_steps(monkeypatch, steps_of(step))

    run_main([SLUG], runner=runner, root=root)
    out_of(capsys)
    run_main([SLUG, "--drill-yes"], runner=runner, root=root)
    out_of(capsys)
    run_main([SLUG, "--drill-yes", "--drill-again"], runner=runner, root=root)
    out_of(capsys)

    assert seen == [(False, False), (True, False), (True, True)], seen


def test_drill_again_alone_never_spends(monkeypatch, root, capsys):
    """--drill-again без разрешения на трату платного прогона не даёт (Д17).

    Отказать можно и раньше проб (флаг без --drill-yes бессмыслен), и на самом
    S13 — сторож требует только результата: денег не потрачено, код 3.
    """
    spent: list = []

    def spend(ctx):
        spent.append(ctx)

    money = FakeStep("S13", Owner.AUTO, [Verdict.OPEN], act_effect=spend)
    patch_steps(monkeypatch, steps_of(money))

    rc = run_main([SLUG, "--drill-again"], runner=RecordingRunner(), root=root)
    out_of(capsys)

    assert spent == [], "--drill-again в одиночку запустил платный прогон"
    assert rc == 3, f"«без --drill-yes дрил не запускается ни разу» -> код 3, получено {rc}"


def test_context_carries_root_slug_and_the_injected_runner(monkeypatch, root, runner, capsys):
    """§12.2/§12.7 п.3: корень, слаг, время и раннер приходят от вызывающего."""
    seen: list = []

    def record(ctx):
        seen.append(ctx)
        return _result("S4", Verdict.CLOSED)

    step = FakeStep("S4", Owner.AUTO, [Verdict.CLOSED])
    step.probe = record  # type: ignore[method-assign]
    patch_steps(monkeypatch, steps_of(step))

    run_main([SLUG], runner=runner, root=root)
    out_of(capsys)

    assert seen, "проба не вызвана"
    ctx = seen[0]
    assert Path(ctx.root) == Path(root), (ctx.root, root)
    assert ctx.slug == SLUG
    assert ctx.runner is runner, "подставной раннер не доехал до Ctx — шов §12.7 п.3 не работает"
    assert isinstance(ctx.now, float), f"§12.6 п.6: Ctx.now — unix-секунды float, получено {ctx.now!r}"


def test_drill_permission_is_not_remembered_between_runs(monkeypatch, root, capsys):
    """--drill-yes действует на ОДИН запуск: следующий снова останавливается (Д17)."""
    marker = Path(root) / "state" / "drills" / SLUG / "run.md"
    spent: list = []

    def spend(ctx):
        if not ctx.drill_yes:
            return None
        spent.append(ctx)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("прогон acme", encoding="utf-8")

    def build():
        class MoneyStep(FakeStep):
            def probe(self, ctx):
                self.probe_calls.append(ctx)
                verdict = Verdict.CLOSED if marker.exists() else Verdict.OPEN
                return _result(self.id, verdict)

            def act(self, ctx):
                self.act_calls.append(ctx)
                spend(ctx)
                if not ctx.drill_yes:
                    return _result(
                        self.id,
                        Verdict.OPEN,
                        waits=True,
                        why="дрил спишет ~$0.29",
                        todo="повтори с --drill-yes",
                    )
                return _result(self.id, Verdict.CLOSED)

        return MoneyStep("S13", Owner.AUTO, [Verdict.OPEN])

    # 1) без флага — останов перед тратой, денег не потрачено
    first = build()
    patch_steps(monkeypatch, steps_of(first))
    rc_first = run_main([SLUG], runner=RecordingRunner(), root=root)
    out_of(capsys)
    assert rc_first == 3, f"без --drill-yes дрил обязан ждать человека, код {rc_first}"
    assert spent == [], "деньги потрачены без разрешения"

    # 2) с флагом — прогон состоялся
    second = build()
    patch_steps(monkeypatch, steps_of(second))
    rc_second = run_main([SLUG, "--drill-yes"], runner=RecordingRunner(), root=root)
    out_of(capsys)
    assert rc_second == 0, f"с --drill-yes прогон обязан состояться, код {rc_second}"
    assert len(spent) == 1

    # 3) факт исчез (новый дрил предстоит), флага нет — снова останов
    marker.unlink()
    third = build()
    patch_steps(monkeypatch, steps_of(third))
    rc_third = run_main([SLUG], runner=RecordingRunner(), root=root)
    out_of(capsys)
    assert rc_third == 3, (
        f"разрешение на трату запомнилось между запусками (код {rc_third}) — "
        "владелец обязан видеть списание ДО него (решение q4)"
    )
    assert len(spent) == 1, "второй прогон списал деньги без флага"


def test_repeated_run_on_a_finished_connect_does_nothing(monkeypatch, root, runner, capsys):
    """Идемпотентность §1: повтор на завершённом подключении не действует."""
    done_a = FakeStep("S11", Owner.AUTO, [Verdict.CLOSED])
    done_b = FakeStep("S13", Owner.AUTO, [Verdict.CLOSED])
    patch_steps(monkeypatch, steps_of(done_a, done_b))

    rc_first = run_main([SLUG], runner=runner, root=root)
    out_of(capsys)
    before = snapshot(root)

    second_a = FakeStep("S11", Owner.AUTO, [Verdict.CLOSED])
    second_b = FakeStep("S13", Owner.AUTO, [Verdict.CLOSED])
    patch_steps(monkeypatch, steps_of(second_a, second_b))
    second_runner = RecordingRunner()
    rc_second = run_main([SLUG], runner=second_runner, root=root)
    out_of(capsys)

    assert rc_first == rc_second == 0
    assert second_a.acts == 0 and second_b.acts == 0, "повтор выполнил действия заново"
    assert second_runner.calls == [], "повтор сходил наружу"
    changed = {
        k
        for k in set(snapshot(root)) | set(before)
        if snapshot(root).get(k) != before.get(k)
    }
    assert changed <= {str(journal_path(root).relative_to(root))}, (
        f"повторный запуск изменил на диске больше журнала: {sorted(changed)}"
    )


# ==========================================================================
# 9. Живой состав шагов: команда обязана вернуть один из четырёх кодов
# ==========================================================================


def test_real_steps_on_a_bare_root_return_one_of_four_codes(root, capsys):
    """Настоящий STEPS, пустой корень, взрывающийся раннер — не трассировка.

    Ни одной живой команды: раннер бросает так же, как бросил бы ненайденный
    исполняемый (§12.6 п.3). Единственный вход обязан ответить кодом, а не
    исключением, и уж точно не сказать «готово».
    """
    exploding = RecordingRunner(raises=FileNotFoundError("pwsh не найден"))

    rc = run_main([SLUG], runner=exploding, root=root)
    text = out_of(capsys)

    assert rc in (1, 2, 3), f"пустой корень выдал код {rc}"
    assert_no_traceback(text)
    assert text.strip(), "команда промолчала на пустом корне"
    if STOP_PREFIX in text:
        stop = parse_stop(text)
        assert stop.why and stop.todo
        assert stop.todo.rstrip().rstrip(".").rstrip().endswith(REPEAT_TAIL)
