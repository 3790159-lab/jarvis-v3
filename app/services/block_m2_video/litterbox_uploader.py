# -*- coding: utf-8 -*-
"""Litterbox (catbox.moe) upload helper.

Uploads a local file to https://litterbox.catbox.moe with a configurable
retention window and returns the public URL. Used by the video pipeline to
expose generated MP4s via short-lived shareable links.
"""
from __future__ import annotations

from pathlib import Path

import httpx

_LITTERBOX_ENDPOINT = "https://litterbox.catbox.moe/resources/internals/api.php"
_VALID_RETENTIONS = {"1h", "12h", "24h", "72h"}


class LitterboxError(RuntimeError):
    """Raised when litterbox upload fails or returns invalid data."""


async def upload_to_litterbox(
    file_path: Path,
    *,
    retention: str = "24h",
    http_client: httpx.AsyncClient | None = None,
    timeout_sec: float = 60.0,
) -> str:
    """Upload a file to litterbox.catbox.moe and return its public URL.

    retention: "1h" | "12h" | "24h" | "72h"
    Raises LitterboxError on network failure or invalid response.
    """
    if retention not in _VALID_RETENTIONS:
        raise LitterboxError(
            f"invalid retention {retention!r}; expected one of "
            f"{sorted(_VALID_RETENTIONS)}"
        )

    path = file_path if isinstance(file_path, Path) else Path(file_path)
    if not path.exists() or not path.is_file():
        raise LitterboxError(f"file not found: {path}")

    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=timeout_sec)
    try:
        with path.open("rb") as fh:
            files = {"fileToUpload": (path.name, fh, "application/octet-stream")}
            data = {"reqtype": "fileupload", "time": retention}
            try:
                response = await client.post(
                    _LITTERBOX_ENDPOINT, data=data, files=files
                )
            except httpx.RequestError as exc:
                raise LitterboxError(f"network error: {exc}") from exc
    finally:
        if owns_client:
            await client.aclose()

    if response.status_code >= 400:
        raise LitterboxError(
            f"HTTP {response.status_code}: {response.text[:200]}"
        )

    url = response.text.strip()
    if not url.startswith("https://"):
        raise LitterboxError(f"invalid response body: {url[:200]!r}")
    return url
