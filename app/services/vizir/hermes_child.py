# -*- coding: utf-8 -*-
"""Subprocess entry point that runs Hermes ISOLATED from Vizir.

Runs inside the installed Hermes venv (its own python.exe), NOT in the Vizir/bot
process. Why isolated: Hermes' ``run_conversation`` is a 250KB+ agent loop with a
history of process-killing paths (#8049 — os._exit on max_iterations, patched in
our pinned commit 1b376855 but a child guarantees Vizir/the bot survive any
future regression). And Hermes SWALLOWS ``step_callback`` exceptions
(agent/conversation_loop.py:664-666), so the cost-cap cannot be enforced from
inside — the PARENT enforces it by killing this child.

Protocol (stdin/stdout JSON, one object per line):
  stdin  <- {"prompt", "model", "enabled_toolsets", "disabled_toolsets",
             "max_iterations", "task_id"?, "provider"?, "api_key"?, "base_url"?,
             "api_mode"?}
  stdout -> {"type":"cost","cost":<cumulative session_estimated_cost_usd>,"iter":n}
            (one per agent iteration, via step_callback)
         -> {"type":"result", "final_response","completed","turn_exit_reason",
             "partial","failed","cost","tokens","iterations","cleanup_errors"}
            (keys verified against agent/turn_finalizer.py:381 on disk)

Validated on disk (STEP 2, $0):
  AIAgent.__init__ kwargs: model / max_iterations / enabled_toolsets /
    disabled_toolsets / quiet_mode / step_callback / provider / api_key /
    base_url / api_mode  (run_agent.py:382)
  run_conversation(user_message, ..., task_id=...) -> result dict
    (run_agent.py:5431; result keys at turn_finalizer.py:381)
  step_callback(api_call_count, prev_tools)  (conversation_loop.py:664)
  session_estimated_cost_usd accumulates per API call (conversation_loop.py:1973)
"""
import json
import os
import sys


def _emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _require_docker_backend(env):
    """Isolation defence-in-depth (knee #2, Variant A): refuse to run unless the
    Docker terminal backend is actually selected, so Hermes can NEVER silently
    execute on the host — the exact failure of live #1. ``TERMINAL_ENV`` is what
    Hermes' own ``_get_env_config`` reads (terminal_tool.py:1244), so asserting it
    here is equivalent to asserting the backend without importing Hermes."""
    if env.get("TERMINAL_ENV") != "docker":
        raise RuntimeError(
            "isolation guard: TERMINAL_ENV=%r != 'docker' — refusing to run on "
            "the host (knee #2 must execute inside a container)."
            % env.get("TERMINAL_ENV"))


def main():
    cfg = json.loads(sys.stdin.read())

    # Variant A: a WSL child cannot inherit the Windows parent env, so the
    # env_overlay rides inside cfg and is applied here BEFORE importing Hermes
    # (so _get_env_config sees TERMINAL_ENV/image/etc.). No-op for knee #1
    # (Windows child, no env_overlay key) — inherited env is untouched.
    for k, v in (cfg.get("env_overlay") or {}).items():
        os.environ[str(k)] = str(v)
    if cfg.get("child_cwd"):
        os.chdir(cfg["child_cwd"])
    if cfg.get("require_docker"):
        try:
            _require_docker_backend(os.environ)
        except RuntimeError as exc:
            # Never run on host: emit a non-completed result and exit so the
            # parent records a refusal (not charged) instead of host execution.
            _emit({"type": "result", "final_response": "", "completed": False,
                   "turn_exit_reason": "isolation_guard_block", "partial": False,
                   "failed": True, "cost": 0.0, "tokens": 0, "iterations": 0,
                   "error": str(exc)})
            return

    from run_agent import AIAgent

    agent = AIAgent(
        model=cfg.get("model") or "",
        provider=cfg.get("provider"),
        api_key=cfg.get("api_key"),
        base_url=cfg.get("base_url"),
        api_mode=cfg.get("api_mode"),
        enabled_toolsets=cfg.get("enabled_toolsets"),
        disabled_toolsets=cfg.get("disabled_toolsets"),
        max_iterations=int(cfg.get("max_iterations") or 30),
        quiet_mode=True,
        save_trajectories=False,
    )

    # step_callback(api_call_count, prev_tools) — read cumulative cost off the
    # agent (the callback args don't carry it) and stream it so the PARENT can
    # apply the cost-cap. Exceptions here are swallowed by Hermes, so this NEVER
    # tries to stop the run itself — the parent kills us instead.
    def _on_step(api_call_count, prev_tools):
        _emit({"type": "cost",
               "cost": float(getattr(agent, "session_estimated_cost_usd", 0.0) or 0.0),
               "iter": int(api_call_count)})

    agent.step_callback = _on_step

    result = agent.run_conversation(
        user_message=cfg["prompt"], task_id=cfg.get("task_id"))

    _emit({
        "type": "result",
        "final_response": result.get("final_response"),
        "completed": bool(result.get("completed")),
        "turn_exit_reason": result.get("turn_exit_reason"),
        "partial": bool(result.get("partial")),
        "failed": bool(result.get("failed")),
        "cost": float(result.get("estimated_cost_usd") or 0.0),
        "tokens": int(result.get("total_tokens") or 0),
        "iterations": int(result.get("api_calls") or 0),
        "cleanup_errors": result.get("cleanup_errors"),
    })


if __name__ == "__main__":
    main()
