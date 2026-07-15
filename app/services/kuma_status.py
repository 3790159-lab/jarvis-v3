# -*- coding: utf-8 -*-
"""DEV-17: Uptime Kuma status summary for ``/smart_health``.

Reads Kuma's public status-page JSON API (no auth needed — it's the same
data a public status page renders) via two calls: the page itself (monitor
names/ids) and its heartbeat feed (latest up/down per monitor id), then
folds them into one human line per monitor.

``KUMA_STATUS_PAGE_URL`` is expected to look like
``http://<host>:3001/api/status-page/<slug>`` (the URL Kuma's own status
page frontend calls) — the heartbeat URL is derived from it. Kuma may not
be deployed yet, or may be temporarily down: every failure mode here
degrades to an honest one-line message, never a crash or a silent blank.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_TIMEOUT_SEC = 5
_STATUS_PAGE_MARKER = "/api/status-page/"


def _kuma_status_page_url() -> Optional[str]:
    url = os.getenv("KUMA_STATUS_PAGE_URL", "").strip()
    return url or None


def _kuma_heartbeat_url(status_page_url: str) -> str:
    idx = status_page_url.find(_STATUS_PAGE_MARKER)
    if idx == -1:
        return status_page_url
    prefix = status_page_url[: idx + len(_STATUS_PAGE_MARKER)]
    slug = status_page_url[idx + len(_STATUS_PAGE_MARKER):]
    return f"{prefix}heartbeat/{slug}"


def _fetch_json(url: str) -> Optional[dict]:
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT_SEC) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.warning("kuma_status: fetch failed for %s: %s", url, exc)
        return None


def kuma_summary(fetch: Callable[[str], Optional[dict]] = _fetch_json) -> str:
    base = _kuma_status_page_url()
    if not base:
        return "Kuma: не настроен (KUMA_STATUS_PAGE_URL пуст)"

    page = fetch(base)
    if page is None:
        return "Kuma: недоступен (%s)" % base

    heartbeat = fetch(_kuma_heartbeat_url(base)) or {}
    heartbeat_list = heartbeat.get("heartbeatList", {})

    lines = ["📡 Uptime Kuma:"]
    for group in page.get("publicGroupList", []):
        for mon in group.get("monitorList", []):
            mid = str(mon.get("id"))
            beats = heartbeat_list.get(mid) or []
            last = beats[-1] if beats else None
            status = last.get("status") if last else None
            icon = "✅" if status == 1 else ("❌" if status is not None else "❓")
            lines.append("  %s %s" % (icon, mon.get("name", mid)))
    if len(lines) == 1:
        lines.append("  (мониторов не найдено)")
    return "\n".join(lines)
