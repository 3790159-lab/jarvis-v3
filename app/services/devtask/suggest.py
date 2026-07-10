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
# 1500 truncated a real reply (15:57 incident): top-3 full /dev_task drafts
# routinely run past it, cutting the JSON array mid-object.
MAX_OUTPUT_TOKENS = 4000
EST_USD = 0.08  # ориентир на ~4к output токенов sonnet-тира (guard_spend резервирует)

_BACKLOG_ITEM_RE = re.compile(r"^\s*\d+\.\s*`\[ \]`\s*\*\*(.+?)\*\*", re.MULTILINE)
_LOG_LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}:\d{2} \| (\w+)\s*\| ")
_SIZE_VALUES = frozenset({"S", "M", "L"})
_FENCE_RE = re.compile(r"```(?:json)?", re.IGNORECASE)


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
    "скопировать как есть. `size` — оценка объёма (S: часы, M: около дня, L: несколько дней). "
    "Ответ должен быть ТОЛЬКО валидным JSON-массивом целиком — без markdown-фенсов "
    "(```), без преамбулы, без пояснений до или после массива, без обёрток."
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


def _extract_json_array(text: str) -> Optional[str]:
    """Best-effort slice of the first top-level JSON array in ``text``.

    Strips markdown code fences, ignores prose before/after the array, and
    salvages truncated output (max_tokens cutoff mid-array) by keeping only
    the complete top-level objects emitted before the cut and closing the
    array — a live incident hit exactly this (1500 tokens was too tight for
    a full top-3 reply).
    """
    text = _FENCE_RE.sub("", text)
    start = text.find("[")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    last_complete_element_end = None
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
            if depth == 1 and ch == "}":
                last_complete_element_end = i
    if last_complete_element_end is not None:
        return text[start:last_complete_element_end + 1] + "]"
    return None


def parse_suggestions(reply: Optional[str]) -> List[dict]:
    """Defensively parse the LLM's JSON-array reply into <=3 validated suggestions.

    Any malformed/missing-field entry is dropped rather than crashing or
    fabricating a placeholder — an honest empty list beats a lying suggestion.
    """
    if not reply:
        return []
    candidate = _extract_json_array(reply)
    if candidate is None:
        return []
    try:
        raw = json.loads(candidate)
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


def format_suggestions(suggestions: List[dict], raw_reply: Optional[str] = None) -> str:
    """Telegram-friendly text: numbered, draft in a code block for easy copy-paste.

    ``raw_reply``, when given and ``suggestions`` is empty, is echoed (first
    200 chars) so a busted reply can actually be diagnosed — the 15:57
    incident left zero trace of what the LLM sent back.
    """
    if not suggestions:
        text = "🤷 не удалось сгенерировать предложения (пустой/битый ответ LLM)."
        if raw_reply:
            text += ("\n\nСырой ответ LLM (первые 200 симв.):\n```\n%s\n```"
                      % raw_reply.strip()[:200])
        return text
    parts = []
    for i, s in enumerate(suggestions, start=1):
        parts.append(
            "%d. **%s** [%s]\n   сигнал: %s\n   почему: %s\n   черновик /dev_task:\n```\n%s\n```"
            % (i, s["title"], s["size"], s["signal"] or "—", s["rationale"] or "—", s["draft"])
        )
    return "\n\n".join(parts)
