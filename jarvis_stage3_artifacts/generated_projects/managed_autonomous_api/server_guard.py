from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
STATE_DIR = PROJECT_ROOT / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)

GUARD_STATE_FILE = STATE_DIR / "guard_state.json"
GUARD_LOG_FILE = STATE_DIR / "guard.log"
GUARD_PID_FILE = STATE_DIR / "guard.pid"
STOP_FLAG_FILE = STATE_DIR / "guard.stop"

STDOUT_LOG = STATE_DIR / "uvicorn_stdout.log"
STDERR_LOG = STATE_DIR / "uvicorn_stderr.log"

HOST = os.environ.get("MANAGED_API_HOST", "127.0.0.1")
PORT = os.environ.get("MANAGED_API_PORT", "8010")

if (PROJECT_ROOT / ".venv" / "Scripts" / "python.exe").exists():
    PYTHON_EXE = str((PROJECT_ROOT / ".venv" / "Scripts" / "python.exe").resolve())
else:
    PYTHON_EXE = sys.executable

RUN_SCRIPT = str((PROJECT_ROOT / "run_api_server.py").resolve())


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n"
    with GUARD_LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line)


def pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
            if handle == 0:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        else:
            os.kill(pid, 0)
            return True
    except Exception:
        return False


def read_existing_guard_pid() -> int | None:
    if not GUARD_PID_FILE.exists():
        return None
    try:
        raw = GUARD_PID_FILE.read_text(encoding="utf-8").strip()
        if not raw:
            return None
        pid = int(raw)
        return pid
    except Exception:
        return None


def ensure_singleton() -> None:
    existing_pid = read_existing_guard_pid()
    current_pid = os.getpid()

    if existing_pid and existing_pid != current_pid and pid_alive(existing_pid):
        print(f"Guard already running with pid {existing_pid}")
        raise SystemExit(0)

    GUARD_PID_FILE.write_text(str(current_pid), encoding="utf-8")


def write_state(child_pid: int | None, restart_count: int, status: str) -> None:
    data = {
        "guard_pid": os.getpid(),
        "child_pid": child_pid,
        "restart_count": restart_count,
        "status": status,
        "host": HOST,
        "port": PORT,
        "updated_at": time.time(),
        "run_script": RUN_SCRIPT,
    }
    GUARD_STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def start_child() -> subprocess.Popen:
    stdout_handle = open(STDOUT_LOG, "a", encoding="utf-8")
    stderr_handle = open(STDERR_LOG, "a", encoding="utf-8")

    env = os.environ.copy()
    env["MANAGED_API_HOST"] = HOST
    env["MANAGED_API_PORT"] = PORT

    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    proc = subprocess.Popen(
        [PYTHON_EXE, "-u", RUN_SCRIPT],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=stdout_handle,
        stderr=stderr_handle,
        creationflags=creationflags,
    )
    return proc


def stop_child(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
    except Exception as exc:
        log(f"stop_child error: {exc}")


def cleanup_guard_files() -> None:
    try:
        if GUARD_PID_FILE.exists():
            GUARD_PID_FILE.unlink()
    except Exception:
        pass


def handle_exit(signum=None, frame=None) -> None:
    log("Guard received stop signal.")
    STOP_FLAG_FILE.write_text("stop", encoding="utf-8")
    cleanup_guard_files()
    sys.exit(0)


signal.signal(signal.SIGINT, handle_exit)
signal.signal(signal.SIGTERM, handle_exit)


def main() -> int:
    ensure_singleton()

    restart_count = 0
    child = None

    if STOP_FLAG_FILE.exists():
        STOP_FLAG_FILE.unlink(missing_ok=True)

    log("Guard started.")
    write_state(None, restart_count, "starting")

    try:
        while True:
            if STOP_FLAG_FILE.exists():
                log("Stop flag detected. Exiting guard.")
                stop_child(child)
                write_state(None, restart_count, "stopped")
                STOP_FLAG_FILE.unlink(missing_ok=True)
                cleanup_guard_files()
                return 0

            if child is None or child.poll() is not None:
                if child is not None:
                    code = child.returncode
                    restart_count += 1
                    log(f"Child exited with code {code}. Restart #{restart_count}")

                child = start_child()
                log(f"Child started with pid {child.pid}")
                write_state(child.pid, restart_count, "running")

            # keep heartbeat fresh
            child_pid = child.pid if child and child.poll() is None else None
            write_state(child_pid, restart_count, "running")

            time.sleep(min(2 + restart_count, 10))
    finally:
        cleanup_guard_files()


if __name__ == "__main__":
    raise SystemExit(main())
