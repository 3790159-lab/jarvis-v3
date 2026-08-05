# -*- coding: utf-8 -*-
"""Ш1 яруса 2 — сборщик снимка: сторож «только чтение» + разбор живых форм.

Сборщик единственный в ярусе имеет право трогать мир, поэтому граница держится
не обещанием в докстринге, а двумя слоями:

  * СТАТИЧЕСКИЙ: в исполняемых PowerShell-константах модуля нет ни одного
    мутирующего глагола, и модуль нигде не зовёт пишущих методов пути;
  * ХОДОВОЙ: каждая команда, которую сборщик РЕАЛЬНО пытается выполнить,
    перехватывается фейком и проверяется по allowlist'у read-only команд.

Статики одной мало: `Get-ScheduledTask | ... | Stop-ScheduledTask` собранный из
кусков пройдёт мимо наивного грепа. Ходовой одной мало тоже: она видит только
те ветки, которые тест прошёл. Вместе — закрывают.
"""
from __future__ import annotations

import ast
import re
from types import SimpleNamespace

from app.services import autonomy_snapshot as snap

# --- мутирующие глаголы PowerShell ------------------------------------------
MUTATING_VERBS = (
    "Register", "Unregister", "Set", "New", "Remove", "Start", "Stop",
    "Restart", "Disable", "Enable", "Add", "Clear", "Move", "Rename",
    "Out", "Write", "Invoke", "Export", "Import", "Suspend", "Resume",
)
_CMDLET_RE = re.compile(r"\b([A-Z][a-zA-Z]+)-[A-Z][a-zA-Z]+\b")

#: Единственные команды, которые сборщику разрешено выполнять.
ALLOWED_CMDLETS = {
    "Get-ScheduledTask", "Get-ScheduledTaskInfo", "Get-Process",
    "Where-Object", "ForEach-Object", "Select-Object", "ConvertTo-Json",
}
ALLOWED_GIT_SUBCOMMANDS = {"rev-parse", "rev-list"}


def _executed_ps_sources() -> dict[str, str]:
    """Строковые константы модуля, которые уходят в PowerShell (`*_PS`)."""
    return {name: value for name, value in vars(snap).items()
            if name.endswith("_PS") and isinstance(value, str)}


# --- слой 1: статический сторож ---------------------------------------------

def test_powershell_constants_carry_no_mutating_verb():
    """Сборщик, умеющий менять ферму, однажды её изменит."""
    sources = _executed_ps_sources()
    assert sources, "не найдено ни одной PS-константы — сторож смотрел бы в пустоту"
    for name, script in sources.items():
        for verb in _CMDLET_RE.findall(script):
            assert verb not in MUTATING_VERBS, (
                f"{name}: мутирующий глагол {verb}- в исполняемом скрипте")


def test_every_cmdlet_is_on_the_read_only_allowlist():
    """Whitelist, а не blacklist: незнакомая команда — это красный тест,
    а не тихо разрешённое действие (fail-closed)."""
    for name, script in _executed_ps_sources().items():
        used = set(re.findall(r"\b[A-Z][a-zA-Z]+-[A-Z][a-zA-Z]+\b", script))
        assert used <= ALLOWED_CMDLETS, f"{name}: вне allowlist — {used - ALLOWED_CMDLETS}"


def test_module_never_calls_a_writing_path_method():
    """Только чтение относится и к диску: `read_text`/`stat` можно, писать — нет."""
    with open(snap.__file__, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    called = {node.func.attr for node in ast.walk(tree)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    forbidden = {"write_text", "write_bytes", "mkdir", "unlink", "rmdir",
                 "rename", "replace", "touch", "chmod", "rmtree", "remove"}
    assert not (called & forbidden), f"пишущие вызовы: {called & forbidden}"


# --- слой 2: ходовой сторож --------------------------------------------------

class RecordingRun:
    """Фейк `subprocess.run`, который запоминает КАЖДУЮ команду."""

    def __init__(self, stdout: str = "[]"):
        self.stdout = stdout
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        return SimpleNamespace(stdout=self.stdout, stderr="", returncode=0)


def test_no_executed_command_can_mutate_anything(tmp_path):
    """Проверяется то, что уходит в subprocess, а не то, что написано в файле."""
    (tmp_path / "scripts").mkdir()
    run = RecordingRun()
    snap.collect(tmp_path, now=1_000.0, run=run)
    assert run.calls, "сборщик не выполнил ни одной команды — сторож слеп"
    for argv in run.calls:
        if argv[0] == "git":
            assert argv[1:3] == ["-C", str(tmp_path)]
            assert argv[3] in ALLOWED_GIT_SUBCOMMANDS, f"git-команда вне allowlist: {argv}"
            continue
        assert argv[0] == "powershell"
        assert "-NonInteractive" in argv
        for verb in _CMDLET_RE.findall(" ".join(argv)):
            assert verb not in MUTATING_VERBS, f"мутирующая команда ушла в мир: {argv}"


# --- разбор живых форм фермы -------------------------------------------------

FARM_JSON = """[
 {"Name":"JarvisChatterGuardian","State":"Running",
  "Triggers":["BootTrigger","LogonTrigger"],"Repetition":"PT10M",
  "LastResult":2147946720},
 {"Name":"JarvisSniperDetached","State":"Ready","Triggers":[],
  "Repetition":null,"LastResult":0},
 {"Name":"JarvisSomethingBrandNew","State":"Ready","Triggers":[],
  "Repetition":null,"LastResult":1}
]"""


def _tasks(json_text: str = FARM_JSON):
    return snap.collect_tasks(run=RecordingRun(json_text))


def test_known_task_gets_its_expectation_from_the_whitelist():
    guardian = {task.name: task for task in _tasks()}["JarvisChatterGuardian"]
    assert guardian.expected_repetition is True
    assert guardian.repetition_interval == "PT10M"
    assert guardian.last_results == (2147946720,)


def test_deliberately_retired_task_expects_no_repetition():
    sniper = {task.name: task for task in _tasks()}["JarvisSniperDetached"]
    assert sniper.expected_repetition is False
    assert "отставлен" in sniper.note


def test_unknown_task_produces_no_expectation_at_all():
    """Fail-closed в сторону ТИШИНЫ: о незнакомом таске мы не знаем ничего,
    и «почини ему триггер» было бы выдумкой, а не наблюдением."""
    fresh = {task.name: task for task in _tasks()}["JarvisSomethingBrandNew"]
    assert fresh.expected_repetition is False
    assert fresh.refusal_codes == ()


def test_drill_task_carries_its_refusal_code():
    assert snap.FARM_EXPECTATIONS["JarvisDrillNightly"]["refusal_codes"] == (2,)


def test_broken_powershell_output_yields_no_tasks_not_a_crash():
    """Сборщик обязан деградировать в пустоту, а не валить ночной прогон."""
    assert snap.collect_tasks(run=RecordingRun("не-json")) == []


def test_missing_last_result_is_absent_history_not_a_zero():
    tasks = _tasks('[{"Name":"JarvisOpsWatchdog","State":"Ready",'
                   '"Triggers":[],"Repetition":null,"LastResult":null}]')
    assert tasks[0].last_results == ()


# --- register-скрипты --------------------------------------------------------

def _script(root, name, body):
    (root / "scripts").mkdir(exist_ok=True)
    (root / "scripts" / name).write_text(body, encoding="utf-8")


def test_register_script_is_matched_to_a_live_task(tmp_path):
    _script(tmp_path, "register_ops_watchdog.ps1",
            '$TaskName = "JarvisOpsWatchdog"\nRegister-ScheduledTask -TaskName $TaskName\n')
    live = {"JarvisOpsWatchdog": object()}
    found = snap.collect_register_scripts(tmp_path, tasks=live)
    assert [item.task_name for item in found] == ["JarvisOpsWatchdog"]
    assert found[0].task_present is True


def test_script_that_registers_nothing_has_no_task_name(tmp_path):
    """`register_telegram_webhook.ps1` дёргает Bot API, таска не создаёт."""
    _script(tmp_path, "register_telegram_webhook.ps1", 'Invoke-RestMethod $url\n')
    found = snap.collect_register_scripts(tmp_path, tasks={})
    assert found[0].task_name is None


def test_sleeping_feature_is_marked_inactive_with_a_reason(tmp_path):
    _script(tmp_path, "register_ig_schedule_publisher.ps1",
            '$TaskName = "JarvisIgSchedulePublisher"\nRegister-ScheduledTask\n')
    found = snap.collect_register_scripts(tmp_path, tasks={})
    assert found[0].feature_active is False
    assert "ig_scheduled_posts.json" in found[0].inactive_reason


def test_probe_present_makes_the_feature_active(tmp_path):
    _script(tmp_path, "register_ig_schedule_publisher.ps1",
            '$TaskName = "JarvisIgSchedulePublisher"\nRegister-ScheduledTask\n')
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "ig_scheduled_posts.json").write_text("[]", encoding="utf-8")
    found = snap.collect_register_scripts(tmp_path, tasks={})
    assert found[0].feature_active is True


def test_parse_task_name_ignores_a_script_without_registration():
    assert snap.parse_task_name("Write-Host 'hello'") is None
    assert snap.parse_task_name('$TaskName = "X"\nRegister-ScheduledTask') == "X"


# --- git и метки -------------------------------------------------------------

def test_git_ahead_behind_is_parsed(tmp_path):
    class Git:
        def __init__(self):
            self.n = 0

        def __call__(self, argv, **kwargs):
            self.n += 1
            out = "phase-4.0" if self.n == 1 else "0\t2"
            return SimpleNamespace(stdout=out, stderr="", returncode=0)

    assert snap.collect_git(tmp_path, run=Git()) == {
        "branch": "phase-4.0", "ahead": 2, "behind": 0}


def test_git_without_upstream_reports_zeroes_not_a_crash(tmp_path):
    """Ветка без origin — обычное дело в worktree; это не повод падать."""
    assert snap.collect_git(tmp_path, run=RecordingRun(""))["ahead"] == 0


def test_missing_stamp_is_none_not_infinity(tmp_path):
    assert snap.collect_stamps(tmp_path, now=1_000.0)["healthchecks_age_sec"] is None


def test_stamp_age_is_measured_from_the_given_now(tmp_path):
    (tmp_path / "state").mkdir()
    stamp = tmp_path / snap.HEALTHCHECKS_STAMP
    stamp.write_text("ok", encoding="utf-8")
    import os
    os.utime(stamp, (500.0, 500.0))
    age = snap.collect_stamps(tmp_path, now=1_000.0)["healthchecks_age_sec"]
    assert abs(age - 500.0) < 2.0
