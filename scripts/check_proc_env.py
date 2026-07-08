# -*- coding: utf-8 -*-
"""Read a running process's environment block from memory (Windows x64).

Verifies that env vars actually reached a process we did NOT launch ourselves —
e.g. the Telegram bot started in a detached window by start_jarvis.ps1. Reads
PEB -> ProcessParameters -> Environment via NtQueryInformationProcess +
ReadProcessMemory. Needs to run as the same/elevated user as the target.

Usage:
    python scripts/check_proc_env.py <PID> [VAR1 VAR2 ...]
If no VARs given, checks the occlusion-test trio.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

DEFAULT_VARS = (
    "VIDEO_SWAP_OCCLUSION_MASK",
    "FACE_SWAP_POD_ID",
    "FACE_SWAP_KEEP_POD_RUNNING",
)

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010

ntdll = ctypes.WinDLL("ntdll")
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class PROCESS_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("Reserved1", ctypes.c_void_p),
        ("PebBaseAddress", ctypes.c_void_p),
        ("Reserved2", ctypes.c_void_p * 2),
        ("UniqueProcessId", ctypes.c_void_p),
        ("Reserved3", ctypes.c_void_p),
    ]


def _read(h, addr, size):
    buf = ctypes.create_string_buffer(size)
    read = ctypes.c_size_t(0)
    ok = kernel32.ReadProcessMemory(
        h, ctypes.c_void_p(addr), buf, size, ctypes.byref(read)
    )
    if not ok:
        raise OSError(f"ReadProcessMemory @ {addr:#x} failed: {ctypes.get_last_error()}")
    return buf.raw[: read.value]


def _ptr(h, addr):
    return int.from_bytes(_read(h, addr, 8), "little")


def read_env(pid: int) -> dict[str, str]:
    h = kernel32.OpenProcess(
        PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid
    )
    if not h:
        raise OSError(f"OpenProcess({pid}) failed: {ctypes.get_last_error()}")
    try:
        pbi = PROCESS_BASIC_INFORMATION()
        status = ntdll.NtQueryInformationProcess(
            h, 0, ctypes.byref(pbi), ctypes.sizeof(pbi), None
        )
        if status != 0:
            raise OSError(f"NtQueryInformationProcess failed: {status:#x}")
        peb = ctypes.cast(pbi.PebBaseAddress, ctypes.c_void_p).value
        # x64 offsets: PEB.ProcessParameters=0x20,
        # RTL_USER_PROCESS_PARAMETERS.Environment=0x80, EnvironmentSize=0x3F0
        params = _ptr(h, peb + 0x20)
        env_addr = _ptr(h, params + 0x80)
        env_size = int.from_bytes(_read(h, params + 0x3F0, 8), "little")
        if env_size <= 0 or env_size > 1_000_000:
            env_size = 64 * 1024
        raw = _read(h, env_addr, env_size)
        text = raw.decode("utf-16-le", errors="replace")
        env = {}
        for entry in text.split("\x00"):
            if "=" in entry[1:]:
                k, _, v = entry.partition("=")
                env[k] = v
        return env
    finally:
        kernel32.CloseHandle(h)


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python scripts/check_proc_env.py <PID> [VAR ...]")
        return 2
    pid = int(sys.argv[1])
    wanted = sys.argv[2:] or list(DEFAULT_VARS)
    env = read_env(pid)
    print(f"[env of PID {pid}]")
    ok = True
    for k in wanted:
        if k in env:
            print(f"  {k} = {env[k]!r}")
        else:
            print(f"  {k} = <NOT SET>")
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
