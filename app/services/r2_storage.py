# -*- coding: utf-8 -*-
"""Cloudflare R2 media hosting (Этап 3, кирпич 3a — замена litterbox).

Uploads a local file to a Cloudflare R2 bucket (S3-compatible) and returns a
public URL. R2 is free within our limits, so no ``record_cost`` is involved —
but a failed upload is an honest delivery error, never a silent success.

Config is read from the environment (loaded from ``.env`` in prod):

  R2_ACCOUNT_ID          Cloudflare account id
  R2_ACCESS_KEY_ID       S3 access key (R2 API token)
  R2_SECRET_ACCESS_KEY   S3 secret
  R2_BUCKET              bucket name (e.g. ``jarvis-media``)
  R2_ENDPOINT            S3 endpoint, e.g. https://<acct>.r2.cloudflarestorage.com
  R2_PUBLIC_BASE_URL     public host for serving objects, e.g.
                         https://pub-<hash>.r2.dev (the bucket's Public
                         Development URL) or a custom domain.

The S3 credentials cannot toggle a bucket's public access — that is a
dashboard-only action (bucket Settings -> Public access -> Public Development
URL). Once enabled, put the r2.dev host into ``R2_PUBLIC_BASE_URL`` and this
module builds ``<base>/<key>`` links from it.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectionError as BotoConnectionError,
    EndpointConnectionError,
)

logger = logging.getLogger(__name__)

__all__ = [
    "R2Config",
    "R2Error",
    "R2ConfigError",
    "R2TransientError",
    "upload_file",
    "upload_file_async",
]

# Extension -> Content-Type. Serving R2 objects with a correct Content-Type is
# what lets Telegram/browsers inline-preview them instead of forcing a download.
_CONTENT_TYPES = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
_DEFAULT_CONTENT_TYPE = "application/octet-stream"

_REQUIRED_ENV = (
    "R2_ACCOUNT_ID",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_BUCKET",
    "R2_ENDPOINT",
    "R2_PUBLIC_BASE_URL",
)


class R2Error(RuntimeError):
    """Raised when an R2 upload fails or is misconfigured."""


class R2ConfigError(R2Error):
    """Raised when required R2 environment variables are missing."""


class R2TransientError(R2Error):
    """Raised when an R2 upload keeps failing on transient (retryable) errors."""


@dataclass
class R2Config:
    account_id: str
    access_key_id: str
    secret_access_key: str
    bucket: str
    endpoint: str
    public_base_url: str


def _load_config() -> R2Config:
    values = {name: os.getenv(name, "").strip() for name in _REQUIRED_ENV}
    missing = [name for name, val in values.items() if not val]
    if missing:
        raise R2ConfigError(
            "missing R2 config: " + ", ".join(missing) + ". Enable the bucket's "
            "Public Development URL in the Cloudflare dashboard and set "
            "R2_PUBLIC_BASE_URL to it."
        )
    return R2Config(
        account_id=values["R2_ACCOUNT_ID"],
        access_key_id=values["R2_ACCESS_KEY_ID"],
        secret_access_key=values["R2_SECRET_ACCESS_KEY"],
        bucket=values["R2_BUCKET"],
        endpoint=values["R2_ENDPOINT"],
        public_base_url=values["R2_PUBLIC_BASE_URL"],
    )


def _make_client(config: R2Config) -> Any:
    # Imported lazily so tests that inject a mock client never construct boto3.
    import boto3
    from botocore.config import Config as BotoConfig

    return boto3.client(
        "s3",
        endpoint_url=config.endpoint,
        aws_access_key_id=config.access_key_id,
        aws_secret_access_key=config.secret_access_key,
        region_name="auto",
        config=BotoConfig(
            signature_version="s3v4",
            retries={"max_attempts": 0, "mode": "standard"},
        ),
    )


def _build_key(path: Path) -> str:
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    ext = path.suffix.lower()
    return f"media/{month}/{uuid.uuid4().hex}{ext}"


def _content_type(key: str) -> str:
    ext = Path(key).suffix.lower()
    return _CONTENT_TYPES.get(ext, _DEFAULT_CONTENT_TYPE)


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, (EndpointConnectionError, BotoConnectionError)):
        return True
    if isinstance(exc, ClientError):
        status = (
            exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if isinstance(exc.response, dict)
            else None
        )
        return isinstance(status, int) and status >= 500
    # Timeouts and other low-level boto errors surface as BotoCoreError.
    name = type(exc).__name__
    return isinstance(exc, BotoCoreError) and "Timeout" in name


def upload_file(
    path: str | Path,
    key: str | None = None,
    *,
    client: Any | None = None,
    config: R2Config | None = None,
    max_attempts: int = 3,
    backoff_base: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Upload ``path`` to R2 and return its public URL.

    key: object key; auto-generated as ``media/YYYY-MM/<uuid>.<ext>`` if None.
    Retries transient errors (5xx, connection/timeout) up to ``max_attempts``.

    Raises:
        R2ConfigError: required env missing (when no ``config`` injected).
        R2TransientError: all attempts exhausted on transient errors.
        R2Error: file missing or a non-retryable upload failure.
    """
    # Validate config first (fail fast on misconfig, independent of the file).
    config = config or _load_config()

    path = Path(path)
    if not path.exists() or not path.is_file():
        raise R2Error(f"file not found: {path}")

    client = client or _make_client(config)
    key = key or _build_key(path)

    attempt = 0
    while True:
        attempt += 1
        try:
            with path.open("rb") as fh:
                client.put_object(
                    Bucket=config.bucket,
                    Key=key,
                    Body=fh,
                    ContentType=_content_type(key),
                )
            break
        except Exception as exc:  # noqa: BLE001 — classify then re-raise honestly
            if _is_transient(exc):
                if attempt >= max_attempts:
                    raise R2TransientError(
                        f"R2 upload failed after {attempt} attempts: {exc}"
                    ) from exc
                delay = backoff_base * (2 ** (attempt - 1))
                logger.warning(
                    "R2 upload transient error (attempt %d/%d), retrying in %.1fs: %s",
                    attempt,
                    max_attempts,
                    delay,
                    exc,
                )
                sleep(delay)
                continue
            raise R2Error(f"R2 upload failed: {exc}") from exc

    public_url = f"{config.public_base_url.rstrip('/')}/{key}"
    logger.info("R2 upload OK: %s -> %s", path.name, public_url)
    return public_url


async def upload_file_async(
    path: str | Path,
    key: str | None = None,
    **kwargs: Any,
) -> str:
    """Async wrapper around :func:`upload_file` (boto3 is blocking)."""
    import asyncio

    return await asyncio.to_thread(upload_file, path, key, **kwargs)
