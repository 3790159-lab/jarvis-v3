#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""DEV-11 detached dev-task launcher: this process IS the survivor.

Spawned by the bot via ``runner.spawn_launcher`` through WMI
``Win32_Process.Create`` (parented to ``WmiPrvSE.exe``, NOT the bot), so a
`taskkill /T` aimed at the bot's own PID (guardian ``Stop-OldBot`` /
``infra_restart_bot_watcher.ps1``) cannot reach it or the CC child it spawns.

It runs the exact same ``runner.run()`` the bot used to run synchronously in
its own thread, then persists the result to ``cc_result.json`` — the bot
never waits on this process; it polls that file on its own heartbeat tick
(state via files, not process ownership — see app/services/devtask/runner.py
DEV-11 section for the full rationale).

Usage: ``python scripts/devtask_cc_launcher.py <task_id>`` — cwd MUST be the
bot's own repo root (wired via WMI ``CurrentDirectory``) so the task's queue
record resolves at ``state/dev_tasks/<task_id>.json``.
"""
import sys
import uuid as _uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.devtask import runner as _r  # noqa: E402
from app.services.devtask.queue import DevTaskQueue  # noqa: E402


def main(argv, queue=None) -> int:
    if len(argv) < 2:
        return 2
    task_id = argv[1]
    q = queue if queue is not None else DevTaskQueue()
    task_dir = Path(q._base) / task_id
    result_path = str(task_dir / "cc_result.json")
    item = q.get(task_id)
    if not item or not item.get("worktree"):
        _r.write_cc_result(result_path, {
            "status": "failed",
            "reason": "launcher_error: no worktree recorded for task %s" % task_id,
        })
        return 1
    try:
        wt = item["worktree"]
        session_uuid = item.get("session_id") or str(_uuid.uuid4())
        report_path = str(Path(wt) / "state" / "dev_tasks" / task_id / "report.md")
        stderr_path = str(task_dir / "stderr.log")
        prompt = _r.build_prompt(task_id, item.get("desc", ""))
        argv_cc = _r.build_argv(wt, session_uuid, prompt,
                                model=_r.resolve_task_model(item.get("desc", "")))
        res = _r.run(argv=argv_cc, cwd=wt, report_path=report_path, stderr_path=stderr_path)
    except Exception as exc:
        res = {"status": "failed", "reason": "launcher_error: %s" % exc}
    _r.write_cc_result(result_path, res)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
