"""Логирование СТРУКТУРЫ собранного system-промпта (спека 2026-07-24 §8).

Мотив: до арки «что ушло в модель» восстанавливали по версиям профиля + llm_usage
(слепая зона). Здесь — на каждую генерацию brain структурная строка (длины
блоков + sha256 + счётчики обязательств), БЕЗ ПДн; и полный дамп ТОЛЬКО по флагу
CHATTER_PROMPT_DUMP (полный промпт содержит профиль/переписку → ПДн, в always-on
логе это утечка; объём ~30-40КБ/ход — тоже против always-on).

Гейтится вызывающей стороной под тем же CHATTER_OBLIGATIONS_SLOT (структурная
строка «всегда», пока арка включена; при выключенной арке — тишина, byte-identical).
"""
from __future__ import annotations

import hashlib
import logging
import os
from collections import Counter
from pathlib import Path

log = logging.getLogger("chatter.core.prompt_log")


def dump_enabled() -> bool:
    return os.getenv("CHATTER_PROMPT_DUMP", "").strip().lower() in (
        "1", "true", "yes", "on")


def _contact_tag(contact_id: str) -> str:
    """Короткий хеш контакта для корреляции БЕЗ раскрытия id (ПДн-минимизация:
    в структурную строку не кладём ни сырой id, ни имя)."""
    if not contact_id:
        return "-"
    return hashlib.sha256(contact_id.encode("utf-8")).hexdigest()[:8]


def obligations_digest(obligations) -> str:
    """PII-free сводка слота: СЧЁТЧИКИ + kinds/statuses. НИ detail, НИ имён,
    НИ msg_id — только форма."""
    obligations = list(obligations or ())
    if not obligations:
        return "n=0"
    by_status = Counter(o.status for o in obligations)
    open_kinds = sorted({o.kind for o in obligations if o.status == "open"})
    deliv_kinds = sorted({o.kind for o in obligations if o.status == "delivered"})
    # renderable = открытые owed_by=bot: РОВНО то, что render_slot_block инъектит в
    # brain (секция ВІДКРИТІ). Отделено от n= (все owed_by), чтобы «посчитано» и
    # «доедет до модели» нельзя было спутать — client-owed brief даёт n>0,
    # renderable=0 (баг Д-10 2026-07-24).
    renderable = sum(
        1 for o in obligations if o.status == "open" and o.owed_by == "bot")
    parts = [f"n={len(obligations)}",
             f"renderable={renderable}",
             "status=" + ",".join(f"{k}:{v}" for k, v in sorted(by_status.items()))]
    if open_kinds:
        parts.append("open=[" + ",".join(open_kinds) + "]")
    if deliv_kinds:
        parts.append("delivered=[" + ",".join(deliv_kinds) + "]")
    return " ".join(parts)


def log_usage_shape(rec: dict) -> None:
    """Строка на КАЖДЫЙ LLM-вызов: теги и числа, ни байта содержимого — тот же
    стандарт ПДн, что у `prompt-shape` (§8).

    Мотив (спека 2026-07-25 §6): до неё «что мы собрали» логировалось, а «что
    из этого доехало в кэш» — нет. Регрессия 23.07 прожила сутки именно в этой
    слепой зоне. `cached=hit|miss` — тот самый второй конец.

    `ttl=` показывает, по какой ставке оплачена запись (5m $3.75/M против 1h
    $6/M): в счёте это треть разницы, а по суммарному счётчику неразличимо."""
    cw = rec.get("cache_creation_input_tokens", 0) or 0
    cr = rec.get("cache_read_input_tokens", 0) or 0
    h1 = rec.get("cache_creation_1h", 0) or 0
    m5 = rec.get("cache_creation_5m", 0) or 0
    ttl = "1h" if h1 else ("5m" if m5 else "-")
    log.info("llm-usage tag=%s model=%s in=%d out=%d cr=%d cw=%d ttl=%s cached=%s",
             rec.get("tag", "-"), rec.get("model", "-"),
             rec.get("input_tokens", 0) or 0, rec.get("output_tokens", 0) or 0,
             cr, cw, ttl, "hit" if cr > 0 else "miss")


def log_funnel_signal(*, contact_id: str, signal: str | None,
                      from_state: str, to_state: str, escalated: bool) -> None:
    """Строка на КАЖДУЮ оценку воронки — включая холостую (§слепая зона).

    Мотив: `stage_signal` не логировался нигде. В БД (`funnel_transitions`)
    намеренно пишется только РЕАЛЬНАЯ смена состояния — холостой ход
    пропускается, чтобы не раздувать метрику «квалифицировано». Значит
    проглоченный сигнал не оставлял следа ни в БД, ни в логе, и дыра
    `new + interested` (закрыта 2026-08-09) прожила незамеченной именно здесь.
    `changed=no` — тот самый второй конец: сигнал пришёл, воронка не сдвинулась.

    ПДн: contact_id хешируется тем же `_contact_tag`, что и в prompt-shape;
    остальное — служебные enum'ы. Стоимость нулевая, сети нет."""
    log.info("funnel contact=%s signal=%s %s->%s changed=%s escalated=%s",
             _contact_tag(contact_id), signal or "-", from_state, to_state,
             "yes" if to_state != from_state else "no",
             "yes" if escalated else "no")


def _dump_dir() -> Path:
    return Path(os.getenv("CHATTER_PROMPT_DUMP_DIR", "logs"))


def log_prompt_shape(*, system: str, suffix: str | None, obligations=(),
                     tag: str = "brain", contact_id: str = "") -> str:
    """Структурная строка (INFO, PII-free) + полный дамп по флагу. Возвращает
    sha (для тестов/корреляции). Блоки: sys = кэшируемый префикс (персона+знания+
    плейбук+правила+примеры), suffix = профиль+обязательства+заметка."""
    suffix = suffix or ""
    full = system + (("\n\n" + suffix) if suffix else "")
    sha = hashlib.sha256(full.encode("utf-8")).hexdigest()[:12]
    log.info(
        "prompt-shape tag=%s contact=%s sys_chars=%d suffix_chars=%d sha=%s obl=%s",
        tag, _contact_tag(contact_id), len(system), len(suffix), sha,
        obligations_digest(obligations))
    if dump_enabled():
        try:
            d = _dump_dir()
            d.mkdir(parents=True, exist_ok=True)
            with (d / "prompt_dump.log").open("a", encoding="utf-8") as f:
                f.write(f"\n===== tag={tag} contact={_contact_tag(contact_id)} "
                        f"sha={sha} sys_chars={len(system)} suffix_chars={len(suffix)} =====\n")
                f.write(full)
                f.write("\n")
        except Exception:  # дамп — вспомогательный, его сбой не роняет ответ лиду
            log.exception("prompt dump write failed (не критично)")
    return sha
