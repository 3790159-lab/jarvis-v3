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
