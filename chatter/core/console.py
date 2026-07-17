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


SOURCE_LABELS = {
    "human_takeover": "вы вмешались",
    "command": "команда /pause",
}


@dataclass(frozen=True)
class PauseView:
    """Готовая к печати строка о паузе. Раннер разрешает имя/ссылку (это
    Telethon), форматтер остаётся чистым."""
    title: str
    link: str
    since_ts: float
    source: str
    detail: str | None
    msg_id: int | None
    resume_eta_ts: float | None


def _hhmm(ts: float) -> str:
    import datetime as _dt
    return _dt.datetime.fromtimestamp(ts).strftime("%H:%M")


def _humanize_gap(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{int(seconds)}с"
    if seconds < 3600:
        return f"{int(seconds // 60)}м"
    return f"{int(seconds // 3600)}ч {int((seconds % 3600) // 60)}м"


def format_status(
    *, kill_switch: bool, pauses: list[PauseView], counters: dict[str, int],
    autoresume_beat_age: float | None, autoresume_interval: float,
    now: float, window_hours: int,
) -> str:
    """Ответ на «почему Аня молчит» за 5 секунд, а не расследованием."""
    lines = ["🤖 Аня — статус"]
    if kill_switch:
        lines.append("Глобально: 🔴 ЗАГЛУШЕНА (/stop). Снять: /start")
    else:
        lines.append("Глобально: РАБОТАЕТ")

    lines.append(f"Заглушено диалогов: {len(pauses)}")
    for p in pauses:
        lines.append(f" • {p.title} — {p.link} — с {_hhmm(p.since_ts)}")
        why = SOURCE_LABELS.get(p.source, p.source)
        if p.detail:
            snippet = p.detail if len(p.detail) <= 40 else p.detail[:40] + "…"
            why += f" («{snippet}»"
            why += f", msg {p.msg_id})" if p.msg_id else ")"
        lines.append(f"   причина: {why}")
        if p.resume_eta_ts is not None:
            lines.append(f"   авто-возврат через {_humanize_gap(p.resume_eta_ts - now)}")
        else:
            lines.append("   авто-возврата нет — снимет только /resume")

    c = counters
    lines.append(
        f"За {window_hours}ч: перехватов {c.get('takeover', 0)} · "
        f"неатрибутированных пауз {c.get('unattributed_pause', 0)} · "
        f"неопознанных исходящих {c.get('unknown_outgoing', 0)}")

    # Кто сторожит сторожа: мёртвая задача авто-возврата неотличима от
    # «пауз к возврату нет», если не показать возраст её heartbeat. Отдельно
    # ловим beat_age=None (задача НИ РАЗУ не отработала — раннер только что
    # стартовал, или задача умерла ещё до первого прогона): сравнение None с
    # числом упало бы TypeError, а молчаливый пропуск проверки скрыл бы ровно
    # тот случай, который эта строка обязана заметить (DEV-18).
    if autoresume_beat_age is None:
        lines.append("⚠️ Авто-возврат: НИ РАЗУ не отработал")
    elif autoresume_beat_age > 3 * autoresume_interval:
        lines.append(f"⚠️ Авто-возврат: последний прогон {_humanize_gap(autoresume_beat_age)} назад")
    else:
        lines.append(f"Авто-возврат: последний прогон {_humanize_gap(autoresume_beat_age)} назад")
    return "\n".join(lines)
