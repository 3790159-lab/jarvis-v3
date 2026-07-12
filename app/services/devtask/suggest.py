# -*- coding: utf-8 -*-
"""/suggest_tasks — генератор задач v0 (Master-Plan Этап 2, зачаток самостроительства).

Собирает четыре сигнала (регресс-baseline, нерешённый бэклог Master-Plan,
уже реализованные пункты Master-Plan, свежие ошибки боевого лога), строит
промпт для одного LLM-вызова и разбирает ответ в топ-3 черновика dev-задачи.
Сигнал «уже реализовано» и жёсткая инструкция в системном промпте — защита от
v0.2 бага: генератор предлагал budget-preflight/таргет-режим регресса, давно
закрытые в проде, потому что не сверялся с текущим состоянием. Только
ПРЕДЛОЖЕНИЯ — админ сам копирует понравившийся черновик в ``/dev_task``,
автозапуска нет.

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
_DONE_ITEM_RE = re.compile(r"^\s*\d+\.\s*`\[[xX]\]`\s*\*\*(.+?)\*\*", re.MULTILINE)
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


def done_signal(master_plan_text: Optional[str], limit: int = 8) -> List[str]:
    """Checked ``[x]`` bullet titles from MASTER-PLAN.md — already shipped work.

    Fed back to the LLM as an explicit "don't re-suggest this" list (v0.2 fix:
    the generator proposed budget-preflight and target-mode regress, both long
    closed in prod, because it only read the raw backlog/log signals without
    cross-checking what MASTER-PLAN already marks done).
    """
    items = _DONE_ITEM_RE.findall(master_plan_text or "")
    return [it.strip() for it in items[:limit]]


def merged_task_signal(merged_tasks: Optional[List[Dict[str, object]]],
                       limit: int = 8) -> List[Dict[str, str]]:
    """Normalize merged dev_task pipeline cards (``queue.DevTaskQueue.merged_history``
    shape: ``{"desc": ..., "merged_at": ...}``) into ``{"title", "date"}`` records.

    v0.3 fix: the "уже реализовано" signal used to be MASTER-PLAN checked items
    only, which lag behind — a task can be merged into prod long before someone
    updates the doc, so the generator kept re-suggesting already-shipped work.
    ``desc`` is the raw, often long, /dev_task draft text (queue.py stores no
    short title) — the first line is used as an honest-enough title.
    """
    out: List[Dict[str, str]] = []
    for item in merged_tasks or []:
        desc = str((item or {}).get("desc") or "").strip()
        if not desc:
            continue
        title = desc.splitlines()[0].strip()
        if len(title) > 120:
            title = title[:117].rstrip() + "..."
        merged_at = str((item or {}).get("merged_at") or "").strip()
        date = merged_at[:10] if merged_at else "дата неизвестна"
        out.append({"title": title, "date": date})
        if len(out) >= limit:
            break
    return out


def done_signal_records(master_plan_text: Optional[str],
                        merged_tasks: Optional[List[Dict[str, object]]] = None,
                        limit: int = 8) -> List[Dict[str, Optional[str]]]:
    """Structured "уже реализовано" records: MASTER-PLAN checked items (no known
    date) plus merged dev_task pipeline cards (title + merge date). Feeds both
    the Сигнал 4 prompt text and the post-parse duplicate matcher (v0.3) —
    single source of truth so the two never drift apart.
    """
    records: List[Dict[str, Optional[str]]] = [
        {"title": t, "date": None} for t in done_signal(master_plan_text, limit=limit)
    ]
    records += list(merged_task_signal(merged_tasks, limit=limit))
    return records


def _format_done_record(record: Dict[str, Optional[str]]) -> str:
    title = record.get("title") or ""
    date = record.get("date")
    return "%s (%s)" % (title, date) if date else title


def recent_error_lines(lines: List[str], today: date, since_days: int = 2,
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
                  log_lines: List[str], today: date, since_days: int = 2,
                  merged_tasks: Optional[List[Dict[str, object]]] = None) -> Dict[str, object]:
    """Combine the raw inputs into the signals dict :func:`build_prompt` expects.

    ``done_records`` (v0.3) is the structured twin of ``done`` — same data, kept
    as ``{"title", "date"}`` dicts for the post-parse duplicate matcher
    (:func:`flag_duplicate_suggestions`), which needs the date/title split that
    the flattened prompt string throws away.
    """
    done_records = done_signal_records(master_plan_text, merged_tasks)
    return {
        "regress": regress_signal(baseline),
        "backlog": backlog_signal(master_plan_text),
        "done": [_format_done_record(r) for r in done_records],
        "done_records": done_records,
        "errors": recent_error_lines(log_lines, today, since_days=since_days),
    }


_SYSTEM = (
    "Ты — технический соучредитель проекта Jarvis V3 (Telegram-бот + AI-конвейер). "
    "Тебе даны четыре сигнала: baseline регресса, нерешённые пункты бэклога из "
    "мастер-плана, СИГНАЛ 4 — что уже реализовано (закрытые пункты мастер-плана "
    "И смерджённые dev_task конвейера), последние ошибки боевого лога. Предложи "
    "РОВНО топ-3 dev-задачи, отсортированные по важности. "
    "ВАЖНО: перед тем как предложить задачу, сверь её тему с Сигналом 4 (уже "
    "реализовано) — если тема там уже упомянута как сделанная (даже если по "
    "формулировке из бэклога или ошибки лога кажется, что она ещё не решена), "
    "НЕ ПРЕДЛАГАЙ её снова, пропусти и возьми следующую по важности. Не предлагай "
    "то, что уже есть в списке реализованного. "
    "ВАЖНО: один черновик — РОВНО одна проблема. Не склеивай несколько разных "
    "пунктов бэклога, разных ошибок или разных тем в один черновик /dev_task — "
    "если видишь несколько отдельных проблем, оформи их как отдельные пункты "
    "топ-3, а не как один пакет. "
    "Отвечай СТРОГО JSON-массивом (никакого текста "
    "вне JSON) из объектов вида "
    '{"title": str, "signal": str, "rationale": str, "draft": str, "size": "S"|"M"|"L"}. '
    "`signal` — какой из входных сигналов породил эту задачу. `draft` — готовый "
    "текст для команды /dev_task: краткая конкретная спека на русском на ОДНУ "
    "проблему, которую можно скопировать как есть. `size` — оценка объёма "
    "(S: часы, M: около дня, L: несколько дней). "
    "Ответ должен быть ТОЛЬКО валидным JSON-массивом целиком — без markdown-фенсов "
    "(```), без преамбулы, без пояснений до или после массива, без обёрток."
)


def build_prompt(signals: Dict[str, object]) -> Tuple[str, List[dict]]:
    """System + messages for the single LLM call. Pure text assembly, no network."""
    backlog = signals.get("backlog") or []
    done = signals.get("done") or []
    errors = signals.get("errors") or []
    lines = [
        "Сигнал 1 — регресс: %s" % signals.get("regress", "нет данных"),
        "",
        "Сигнал 2 — бэклог (нерешённые пункты мастер-плана):",
    ]
    lines += (["  • " + b for b in backlog] if backlog else ["  (пусто)"])
    lines += ["", "Сигнал 4 — уже реализовано (НЕ предлагай это снова):"]
    lines += (["  • " + d for d in done] if done else ["  (пусто)"])
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


_WORD_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)


def _tokenize(text: str) -> set:
    return {w for w in _WORD_RE.findall((text or "").lower()) if len(w) > 2}


def _char_ngrams(text: str, n: int = 3) -> set:
    """Character n-grams of the normalized (lowercased, whitespace-collapsed)
    text — used for duplicate matching instead of word tokens because Russian
    inflection («контаминацию» vs «контаминации» vs «контаминация») makes exact
    word overlap miss near-identical titles; shared substrings survive endings
    drifting."""
    t = re.sub(r"\s+", " ", (text or "").lower()).strip()
    if len(t) < n:
        return {t} if t else set()
    return {t[i:i + n] for i in range(len(t) - n + 1)}


def find_duplicate_match(title: str, done_records: Optional[List[Dict[str, object]]],
                         threshold: float = 0.22) -> Optional[Dict[str, object]]:
    """Best ``done_records`` match for ``title`` by character-trigram (Jaccard)
    overlap, or ``None`` — a fuzzy net that catches the LLM re-suggesting done
    work even when its wording drifts from the original backlog/report title
    (the system prompt's "don't re-suggest" instruction alone doesn't guarantee
    this)."""
    cand = _char_ngrams(title)
    if not cand:
        return None
    best, best_score = None, 0.0
    for record in done_records or []:
        ref = _char_ngrams(str(record.get("title") or ""))
        if not ref:
            continue
        score = len(cand & ref) / len(cand | ref)
        if score > best_score:
            best, best_score = record, score
    return best if best is not None and best_score >= threshold else None


def flag_duplicate_suggestions(suggestions: List[dict],
                               done_records: Optional[List[Dict[str, object]]],
                               threshold: float = 0.22) -> List[dict]:
    """Post-parse dedup gate (v0.3): match each candidate's title against
    ``done_records`` (MASTER-PLAN checked items + merged dev_task cards) and
    tag matches with an honest ``duplicate_warning`` instead of silently
    dropping them — the admin still sees the card and decides whether it's a
    false positive or genuinely stale, rather than a candidate vanishing with
    no trace (a live bug: the generator kept re-proposing the log-contamination
    fix and the regress watchdog, both already shipped)."""
    out = []
    for s in suggestions:
        match = find_duplicate_match(s.get("title", ""), done_records, threshold=threshold)
        if match is not None:
            s = dict(s)
            when = match.get("date") or "дата неизвестна"
            s["duplicate_warning"] = "⚠️ возможно уже решено: %s (%s)" % (match.get("title"), when)
        out.append(s)
    return out


def count_matched_backlog_items(draft: str, backlog_items: Optional[List[str]],
                                threshold: float = 0.3) -> int:
    """Number of DISTINCT ``backlog_items`` whose own vocabulary substantially
    shows up in ``draft`` — a high count signals the draft glues several
    unrelated backlog problems into one /dev_task package (v0.3: "один пункт
    бэклога = один черновик")."""
    draft_tokens = _tokenize(draft)
    if not draft_tokens:
        return 0
    count = 0
    for item in backlog_items or []:
        item_tokens = _tokenize(item)
        if not item_tokens:
            continue
        coverage = len(draft_tokens & item_tokens) / len(item_tokens)
        if coverage >= threshold:
            count += 1
    return count


def flag_packaged_suggestions(suggestions: List[dict], backlog_items: Optional[List[str]],
                              threshold: float = 0.3, min_items: int = 2) -> List[dict]:
    """Tag drafts that appear to bundle >= ``min_items`` distinct backlog problems
    into one /dev_task with a ``packaged_warning`` — a package is harder to
    review and to roll back than N separate single-issue drafts."""
    out = []
    for s in suggestions:
        n = count_matched_backlog_items(s.get("draft", ""), backlog_items, threshold=threshold)
        if n >= min_items:
            s = dict(s)
            s["packaged_warning"] = ("⚠️ похоже, черновик объединяет несколько пунктов "
                                     "бэклога — раздели на отдельные /dev_task")
        out.append(s)
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


def format_suggestion_card(suggestion: dict, index: int, total: int) -> str:
    """Compact one-suggestion Telegram card: title/size/signal/rationale only —
    NEVER the ``draft`` body. The old all-in-one message put every draft (a full
    /dev_task spec) into one ``send()``, which silently truncates at ~3900 chars
    and made long drafts uncopyable. The draft now stays server-side (see
    ``build_suggestions_state``) and reaches ``/dev_task`` intact via the
    [🛠 Запустить как dev_task] button, not via chat text.
    """
    warnings = "".join(
        "%s\n" % w for w in (suggestion.get("duplicate_warning"), suggestion.get("packaged_warning")) if w
    )
    return (
        "%s💡 %d/%d **%s** [%s]\n"
        "сигнал: %s\n"
        "почему: %s\n\n"
        "Тап [🛠 Запустить как dev_task] поставит черновик в очередь с обычным "
        "подтверждением (▶️ Запустить) — автозапуска нет."
        % (warnings, index + 1, total, suggestion.get("title", ""), suggestion.get("size", ""),
           suggestion.get("signal") or "—", suggestion.get("rationale") or "—")
    )


def build_suggestions_state(suggestions: List[dict], gen_id: str, created_at: str) -> Dict[str, object]:
    """JSON-serializable snapshot persisted so a button tap can recover the FULL
    draft text later — Telegram's 64-byte ``callback_data`` cap can't carry it.
    ``gen_id`` doubles as the stale-guard (see ``resolve_suggestion``): a new
    ``/suggest_tasks`` run overwrites this with a fresh id, so buttons from a
    previous generation stop resolving instead of silently firing a stale draft.
    """
    return {"gen_id": gen_id, "created_at": created_at, "suggestions": suggestions}


def resolve_suggestion(state: Optional[dict], gen_id: str, index: int) -> Optional[dict]:
    """Look up suggestion ``index`` from a persisted state, iff ``gen_id`` matches
    the CURRENT generation. Any mismatch (no state, stale gen_id, bad index)
    returns ``None`` — the caller must treat that as "устарело, сгенерируй заново"."""
    if not state or state.get("gen_id") != gen_id:
        return None
    items = state.get("suggestions") or []
    if not (0 <= index < len(items)):
        return None
    return items[index]
