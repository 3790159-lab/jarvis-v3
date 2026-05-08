from __future__ import annotations

import builtins
import os
import sys
from pathlib import Path

os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

for stream_name in ("stdin", "stdout", "stderr"):
    stream = getattr(sys, stream_name, None)
    if stream is not None and hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

_original_open = builtins.open


def _patched_open(file, mode="r", buffering=-1, encoding=None, errors=None, newline=None, closefd=True, opener=None):
    text_mode = "b" not in mode
    if text_mode and encoding is None:
        encoding = "utf-8"
    return _original_open(
        file=file,
        mode=mode,
        buffering=buffering,
        encoding=encoding,
        errors=errors,
        newline=newline,
        closefd=closefd,
        opener=opener,
    )


builtins.open = _patched_open

_original_path_open = Path.open
_original_read_text = Path.read_text
_original_write_text = Path.write_text


def _patched_path_open(self, mode="r", buffering=-1, encoding=None, errors=None, newline=None):
    text_mode = "b" not in mode
    if text_mode and encoding is None:
        encoding = "utf-8"
    return _original_path_open(
        self,
        mode=mode,
        buffering=buffering,
        encoding=encoding,
        errors=errors,
        newline=newline,
    )


def _patched_read_text(self, encoding=None, errors=None):
    if encoding is None:
        encoding = "utf-8"
    return _original_read_text(self, encoding=encoding, errors=errors)


def _patched_write_text(self, data, encoding=None, errors=None, newline=None):
    if encoding is None:
        encoding = "utf-8"
    if newline is None:
        newline = "\n"
    return _original_write_text(self, data, encoding=encoding, errors=errors, newline=newline)


Path.open = _patched_path_open
Path.read_text = _patched_read_text
Path.write_text = _patched_write_text
