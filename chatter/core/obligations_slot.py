"""Слот ОТКРЫТЫХ ОБЯЗАТЕЛЬСТВ лида — чистый слой (спека
2026-07-24-chatter-obligations-slot-design.md §3/§5).

Мотив (дрил volska 2026-07-24): «что бот должен лиду» жило только в лоссовой
прозе профиля (сжимается, «закрытые вопросы» выбрасываются первыми) и в окне
истории (уезжает за 24 сообщения). Открытое обязательство старше окна теряется.
Этот слой — структурный носитель: набор строк с явными переходами статуса,
который НЕ сжимается и НЕ зависит от окна.

Ноль I/O: merge/переходы + рендер блока для brain. Хранилище (таблица) и
проводка через classifier — в db.py / classifier.py / run.py. Критерий закрытия
по ВЫПОЛНЕНИЮ ФУНКЦИИ (не по слову) — семантика КЛАССИФИКАТОРА (промпт), здесь
только механика перехода статуса, который он вернул. См. память
`jarvis-obligation-closed-by-function`.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

DAY = 86400.0

KINDS = ("brief", "examples", "recalc", "owner_write", "other")
OWED_BY = ("bot", "client")
STATUSES = ("open", "delivered", "cancelled")
_CLOSED = ("delivered", "cancelled")

DETAIL_MAX = 80          # §5: detail ≤ ~80 симв
RENDER_CAP = 5           # §5: ≤5 открытых + ≤5 недавно-закрытых
RECENT_DAYS = 7          # §5: «ЗАКРИТО НЕДАВНО» — delivered за 7 дней

_OPEN_HEADER = "=== ВІДКРИТІ ЗОБОВ'ЯЗАННЯ (відпрацювати ПЕРЕД хендоффом) ==="
_RECENT_HEADER = "=== ЗАКРИТО НЕДАВНО (НЕ повторювати) ==="


@dataclass(frozen=True)
class Obligation:
    """Одна строка слота. `okey` — ключ (kind, либо 'other:<slug>'): один
    активный на вид (два открытых «brief» бессмысленны). msg_id/ts штампует КОД
    (модель ненадёжно знает id сообщений)."""
    okey: str
    kind: str
    owed_by: str          # bot | client
    status: str           # open | delivered | cancelled
    detail: str
    created_msg_id: int | None
    closed_msg_id: int | None
    created_ts: float
    closed_ts: float | None


def okey_for(kind: str, slug: str | None = None) -> str:
    """Ключ обязательства. Перечисленные виды → сам kind; 'other' → 'other:<slug>'
    (несколько «other» различаются slug'ом)."""
    if kind == "other":
        s = (slug or "").strip() or "misc"
        return f"other:{s}"
    return kind


def _clean_detail(detail: str) -> str:
    return (detail or "").strip()[:DETAIL_MAX]


def merge_obligations(
    existing, updates, *, now: float, current_msg_id: int | None,
):
    """Свести существующие обязательства с обновлениями классификатора.

    `existing` — list[Obligation]; `updates` — list[dict] из classifier JSON
    (`{kind, owed_by, status, detail, slug?}`). Возвращает новый list[Obligation].

    Правила (§4):
    - НЕ упомянутое обновлением обязательство сохраняется как есть (absence ≠
      удаление — мусорный/частичный ход не должен стирать долг).
    - open, которого не было → создаётся, штампуется created_*.
    - open→delivered/cancelled → штампуется closed_* (created_* не трогаем).
    - повторное закрытие уже закрытого → no-op по ts (первое закрытие достоверно).
    - closed→open (переобещали) → сбрасываем closed_*, снова open.
    - delivered/cancelled, которого не было → создаётся сразу закрытым (возникло и
      закрылось в одном окне — brain отвечает ДО классификатора, такое бывает).
    - detail обрезается до DETAIL_MAX.
    Некорректный status/owed_by → обновление игнорируется (защита от мусора).
    """
    by_okey: dict[str, Obligation] = {o.okey: o for o in existing}

    for upd in updates or []:
        kind = (upd.get("kind") or "").strip()
        status = (upd.get("status") or "").strip()
        owed_by = (upd.get("owed_by") or "bot").strip()
        if status not in STATUSES or owed_by not in OWED_BY or not kind:
            continue
        detail = _clean_detail(upd.get("detail", ""))
        slug = upd.get("slug")
        if kind == "other" and not slug:
            slug = detail[:24] or "misc"
        okey = okey_for(kind, slug)

        cur = by_okey.get(okey)
        if cur is None:
            if status == "open":
                by_okey[okey] = Obligation(
                    okey=okey, kind=kind, owed_by=owed_by, status="open",
                    detail=detail, created_msg_id=current_msg_id, closed_msg_id=None,
                    created_ts=now, closed_ts=None)
            else:  # возникло и сразу закрылось
                by_okey[okey] = Obligation(
                    okey=okey, kind=kind, owed_by=owed_by, status=status,
                    detail=detail, created_msg_id=current_msg_id,
                    closed_msg_id=current_msg_id, created_ts=now, closed_ts=now)
            continue

        # обновление существующего
        if status == "open":
            # open (или переоткрытие закрытого): сбрасываем closure
            by_okey[okey] = replace(
                cur, status="open", detail=detail or cur.detail,
                closed_msg_id=None, closed_ts=None)
        else:  # delivered | cancelled
            if cur.status in _CLOSED:
                # уже закрыто — первое закрытие достоверно, ts не двигаем
                by_okey[okey] = replace(cur, status=status,
                                        detail=detail or cur.detail)
            else:
                by_okey[okey] = replace(
                    cur, status=status, detail=detail or cur.detail,
                    closed_msg_id=current_msg_id, closed_ts=now)

    return list(by_okey.values())


def render_slot_block(
    obligations, *, now: float, recent_days: float = RECENT_DAYS, cap: int = RENDER_CAP,
) -> str:
    """Собрать текстовый блок для системного промпта brain из ОБЯЗАТЕЛЬСТВ БОТА.

    Две секции (§5): открытые (отработать перед хендоффом) и недавно-закрытые
    (не повторять уже отданное — симметричное закрытие §1). Рендер ТОЛЬКО из
    структуры, не из окна → долг доезжает, даже если ход-источник уехал за окно.
    Пусто (нет ни открытых, ни свежих delivered) → "" (блок не добавляется)."""
    open_bot = sorted(
        (o for o in obligations if o.owed_by == "bot" and o.status == "open"),
        key=lambda o: (o.created_ts, o.okey))[:cap]
    recent = sorted(
        (o for o in obligations
         if o.owed_by == "bot" and o.status == "delivered"
         and o.closed_ts is not None and o.closed_ts >= now - recent_days * DAY),
        key=lambda o: o.closed_ts, reverse=True)[:cap]

    if not open_bot and not recent:
        return ""

    lines: list[str] = []
    if open_bot:
        lines.append(_OPEN_HEADER)
        lines.extend(f"- {o.kind}: {o.detail}" for o in open_bot)
    if recent:
        if lines:
            lines.append("")
        lines.append(_RECENT_HEADER)
        lines.extend(f"- {o.kind} ✓ — {o.detail}" for o in recent)
    return "\n".join(lines)


def render_current_for_classifier(obligations) -> str:
    """Компактный список ОТКРЫТЫХ обязательств для промпта классификатора — чтобы
    он мог их закрыть (open→delivered/cancelled) по выполнению функции. В отличие
    от render_slot_block (для brain) — без UA-заголовков и без секции «недавно
    закрытых» (классификатор оценивает только активные). Пусто → ""."""
    open_ones = [o for o in obligations if o.status == "open"]
    if not open_ones:
        return ""
    return "\n".join(f"- {o.kind} ({o.owed_by}): {o.detail}" for o in open_ones)
