# -*- coding: utf-8 -*-
"""Клиентский бренд-конфиг генеративных IG-аккаунтов (``clients/<name>/brand.md``).

Формат файла: YAML-frontmatter (машиночитаемый бриф — business/tone/lang/cta/
forbidden/...) + человекочитаемое markdown-тело (рубрики, стратегия, для
ручного ревью и skill smm-instagram §6). :func:`load_brand_config` читает
ТОЛЬКО frontmatter — тело не парсится и не нужно коду.

Чистый модуль: только чтение с диска, ноль сети, ноль денег.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yaml

CLIENTS_DIR = Path(__file__).resolve().parents[2] / "clients"

_CLIENT_ARG_RE = re.compile(r"^client=([\w\-]+)$", re.IGNORECASE)
_NOPERSONA_ARG_RE = re.compile(r"^nopersona$", re.IGNORECASE)


def brand_md_path(client: str) -> Path:
    return CLIENTS_DIR / client / "brand.md"


def load_brand_config(client: Optional[str]) -> Optional[Dict[str, Any]]:
    """Прочитать frontmatter ``clients/<client>/brand.md`` -> dict бриф-полей.

    Нет client / файла / frontmatter / битый YAML -> ``None`` — вызывающий
    код обязан честно фолбэкнуть на текущее поведение без конфига.
    """
    if not client:
        return None
    try:
        text = brand_md_path(client).read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    try:
        data = yaml.safe_load(text[3:end].strip())
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def find_persona_media(persona_id: str) -> Optional[Dict[str, Any]]:
    """Найти ``persona_media`` блок среди всех ``clients/*/brand.md`` по
    ``persona_id`` (для /persona_photo, у которого нет client-контекста —
    боевые параметры и флаг ``refine`` живут per-persona в brand.md).

    Возвращает dict ``persona_media`` первого совпадения или ``None``. Чистое
    чтение с диска, ноль сети/денег.
    """
    if not persona_id:
        return None
    try:
        client_dirs = [p for p in CLIENTS_DIR.iterdir() if p.is_dir()]
    except OSError:
        return None
    for client_dir in client_dirs:
        cfg = load_brand_config(client_dir.name)
        if not cfg:
            continue
        pm = cfg.get("persona_media")
        if isinstance(pm, dict) and str(pm.get("persona_id") or "").strip() == persona_id:
            return pm
    return None


def parse_client_arg(query: Optional[str]) -> Tuple[Optional[str], str]:
    """Вытащить необязательный префикс ``client=<name>`` из текста команды.

    ``"client=vera_ai_ua тема"`` -> ``("vera_ai_ua", "тема")``. Без префикса ->
    ``(None, query.strip())`` — старое поведение не меняется.
    """
    parts = (query or "").strip().split(None, 1)
    if not parts:
        return None, ""
    m = _CLIENT_ARG_RE.match(parts[0])
    if not m:
        return None, (query or "").strip()
    rest = parts[1].strip() if len(parts) > 1 else ""
    return m.group(1), rest


def parse_nopersona_arg(query: Optional[str]) -> Tuple[bool, str]:
    """Вытащить необязательный флаг ``nopersona`` из текста команды.

    Явный оверрайд для ``/ig_gen`` — генерить фото БЕЗ персоны, даже если у
    клиента в ``brand.md`` настроена ``persona_media`` (см. рубрику «Пост
    дня» — чиста естетика без ШІ-аватара). ``"nopersona тема"`` -> ``(True,
    "тема")``. Без флага -> ``(False, query.strip())`` — как ``parse_client_arg``.
    """
    parts = (query or "").strip().split(None, 1)
    if not parts:
        return False, ""
    if not _NOPERSONA_ARG_RE.match(parts[0]):
        return False, (query or "").strip()
    rest = parts[1].strip() if len(parts) > 1 else ""
    return True, rest
