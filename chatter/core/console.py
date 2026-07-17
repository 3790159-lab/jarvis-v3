"""Пульт владельца: чистый разбор команд из Saved Messages (арка 3A).

Ноль Telethon и ноль сети: раннер приносит текст, получает Command."""
from __future__ import annotations

import re
from dataclasses import dataclass

GLOBAL_COMMANDS = frozenset({"status", "stop", "start"})
TARGETED_COMMANDS = frozenset({"pause", "resume"})

_DURATION_RE = re.compile(r"^(\d+)([hm])$", re.IGNORECASE)


@dataclass(frozen=True)
class Command:
    name: str
    duration_seconds: float | None = None
    target: str | None = None      # сырая ссылка/id, если владелец указал явно
    error: str | None = None       # человеческая жалоба вместо тихого игнора


def _parse_duration(token: str) -> float | None:
    m = _DURATION_RE.match(token)
    if not m:
        return None
    value, unit = int(m.group(1)), m.group(2).lower()
    return value * (3600.0 if unit == "h" else 60.0)


def parse_command(text: str) -> Command | None:
    """None = это не команда (обычный текст в Saved Messages трогать нельзя)."""
    parts = (text or "").strip().split()
    if not parts or not parts[0].startswith("/"):
        return None
    name = parts[0][1:].casefold()
    args = parts[1:]

    if name in GLOBAL_COMMANDS:
        return Command(name=name)
    if name not in TARGETED_COMMANDS:
        return None

    duration: float | None = None
    if name == "pause" and args:
        duration = _parse_duration(args[0])
        if duration is not None:
            args = args[1:]
        elif not args[0].startswith(("t.me", "https://", "@")) and not args[0].isdigit():
            # Похоже на кривую длительность, а не на адресата. Сказать вслух:
            # молчаливый игнор = владелец уверен, что пауза стоит (DEV-18).
            return Command(name=name, error=f"не понял длительность: '{args[0]}' (примеры: 1h, 30m)")

    return Command(name=name, duration_seconds=duration, target=args[0] if args else None)
