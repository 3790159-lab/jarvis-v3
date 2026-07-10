# -*- coding: utf-8 -*-
"""/suggest_tasks — генератор задач v0 (Master-Plan Этап 2, зачаток самостроительства).

Собирает три сигнала (регресс-baseline, нерешённый бэклог Master-Plan, свежие
ошибки боевого лога), строит промпт для одного LLM-вызова и разбирает ответ в
топ-3 черновика dev-задачи. Только ПРЕДЛОЖЕНИЯ — админ сам копирует
понравившийся черновик в ``/dev_task``, автозапуска нет.

Чистый модуль: ноль сети, ноль файлового IO. Файлы читает и LLM зовёт (под
``guard_spend``) обвязка в ``tools/jarvis_smart_telegram_control.py`` — та же
изоляция, что у IR-2 (``tools/intent_router.py`` строит промпт, control-файл
шлёт запрос), ради money-safety: тесты мокают только вызов LLM.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

MODEL = "claude-sonnet-4-6"
EST_USD = 0.03  # ориентир на ~1.5к output токенов sonnet-тира (guard_spend резервирует)

_BACKLOG_ITEM_RE = re.compile(r"^\s*\d+\.\s*`\[ \]`\s*\*\*(.+?)\*\*", re.MULTILINE)
_LOG_LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}:\d{2} \| (\w+)\s*\| ")
_SIZE_VALUES = frozenset({"S", "M", "L"})


def regress_signal(baseline: Optional[dict]) -> str:
    """Human-readable regress-baseline signal, or an honest "no baseline" note."""
    if not baseline:
        return "регресс: baseline не задан"
    return ("регресс baseline: %d failed / %d passed / %d errors"
            % (int(baseline.get("failed", 0)), int(baseline.get("passed", 0)),
               int(baseline.get("errors", 0))))


def backlog_signal(master_plan_text: Optional[str], limit: int = 8) -> List[str]:
    """Unchecked ``[ ]`` backlog bullet titles from MASTER-PLAN.md, doc order."""
    items = _BACKLOG_ITEM_RE.findall(master_plan_text or "")
    return [it.strip() for it in items[:limit]]


def recent_error_lines(lines: List[str], today: date, since_days: int = 3,
                       levels: Tuple[str, ...] = ("ERROR", "CRITICAL"),
                       limit: int = 20) -> List[str]:
    """Log lines at ``levels`` within the last ``since_days`` days (by date prefix).

    Unparseable lines (no leading ``YYYY-MM-DD HH:MM:SS | LEVEL |``) are dropped
    rather than guessed — an honest skip beats a wrong bucket. Future-dated lines
    (clock skew) are dropped too.
    """
    out = []
    for line in lines or []:
        m = _LOG_LINE_RE.match(line)
        if not m:
            continue
        d_str, level = m.group(1), m.group(2)
        try:
            d = datetime.strptime(d_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        age = (today - d).days
        if age < 0 or age > since_days:
            continue
        if level not in levels:
            continue
        out.append(line.strip())
    return out[-limit:]


def build_signals(*, baseline: Optional[dict], master_plan_text: Optional[str],
                  log_lines: List[str], today: date, since_days: int = 3) -> Dict[str, object]:
    """Combine the three raw inputs into the signals dict :func:`build_prompt` expects."""
    return {
        "regress": regress_signal(baseline),
        "backlog": backlog_signal(master_plan_text),
        "errors": recent_error_lines(log_lines, today, since_days=since_days),
    }


_SYSTEM = (
    "Ты — технический соучредитель проекта Jarvis V3 (Telegram-бот + AI-конвейер). "
    "Тебе даны три сигнала: baseline регресса, нерешённые пункты бэклога из "
    "мастер-плана, последние ошибки боевого лога. Предложи РОВНО топ-3 dev-задачи, "
    "отсортированные по важности. Отвечай СТРОГО JSON-массивом (никакого текста "
    "вне JSON) из объектов вида "
    '{"title": str, "signal": str, "rationale": str, "draft": str, "size": "S"|"M"|"L"}. '
    "`signal` — какой из трёх входных сигналов породил эту задачу. `draft` — готовый "
    "текст для команды /dev_task: краткая конкретная спека на русском, которую можно "
    "скопировать как есть. `size` — оценка объёма (S: часы, M: около дня, L: несколько дней)."
)


def build_prompt(signals: Dict[str, object]) -> Tuple[str, List[dict]]:
    """System + messages for the single LLM call. Pure text assembly, no network."""
    backlog = signals.get("backlog") or []
    errors = signals.get("errors") or []
    lines = [
        "Сигнал 1 — регресс: %s" % signals.get("regress", "нет данных"),
        "",
        "Сигнал 2 — бэклог (нерешённые пункты мастер-плана):",
    ]
    lines += (["  • " + b for b in backlog] if backlog else ["  (пусто)"])
    lines += ["", "Сигнал 3 — последние ошибки лога:"]
    lines += (["  • " + e for e in errors] if errors else ["  (нет свежих ошибок)"])
    return _SYSTEM, [{"role": "user", "content": "\n".join(lines)}]


def parse_suggestions(reply: Optional[str]) -> List[dict]:
    """Defensively parse the LLM's JSON-array reply into <=3 validated suggestions.

    Any malformed/missing-field entry is dropped rather than crashing or
    fabricating a placeholder — an honest empty list beats a lying suggestion.
    """
    if not reply:
        return []
    start, end = reply.find("["), reply.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        raw = json.loads(reply[start:end + 1])
    except (ValueError, TypeError):
        return []
    if not isinstance(raw, list):
        return []
    out: List[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        draft = str(item.get("draft") or "").strip()
        if not title or not draft:
            continue
        size = str(item.get("size") or "M").strip().upper()
        if size not in _SIZE_VALUES:
            size = "M"
        out.append({
            "title": title,
            "signal": str(item.get("signal") or "").strip(),
            "rationale": str(item.get("rationale") or "").strip(),
            "draft": draft,
            "size": size,
        })
        if len(out) == 3:
            break
    return out


def format_suggestions(suggestions: List[dict]) -> str:
    """Telegram-friendly text: numbered, draft in a code block for easy copy-paste."""
    if not suggestions:
        return "🤷 не удалось сгенерировать предложения (пустой/битый ответ LLM)."
    parts = []
    for i, s in enumerate(suggestions, start=1):
        parts.append(
            "%d. **%s** [%s]\n   сигнал: %s\n   почему: %s\n   черновик /dev_task:\n```\n%s\n```"
            % (i, s["title"], s["size"], s["signal"] or "—", s["rationale"] or "—", s["draft"])
        )
    return "\n\n".join(parts)
