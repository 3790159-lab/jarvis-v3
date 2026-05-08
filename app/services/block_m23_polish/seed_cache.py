# -*- coding: utf-8 -*-
"""Local disk cache for persona seed photos to avoid redundant downloads."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.services.block_m_common.logging_setup import get_logger

logger = get_logger("seed_cache")

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_PERSONAS_DIR = _ROOT / "state" / "personas"
_SENTINEL_FILENAME = ".cache_valid"
_DEFAULT_MAX_AGE_HOURS = 24


class SeedCache:
    """Download and cache seed photos for a persona.

    Files are stored in state/personas/{persona_id}/seeds_cache/.
    A sentinel file (.cache_valid) holds the last-validated timestamp; callers
    can use is_cache_valid() to decide whether a refresh is needed.

    Args:
        storage_dir: Base personas dir. Defaults to state/personas/.
    """

    def __init__(self, storage_dir: Path | None = None) -> None:
        self._root = storage_dir or _PERSONAS_DIR

    def _cache_dir(self, persona_id: str) -> Path:
        d = self._root / persona_id / "seeds_cache"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _sentinel_file(self, persona_id: str) -> Path:
        return self._cache_dir(persona_id) / _SENTINEL_FILENAME

    async def cache_seed(self, persona_id: str, photo_url: str) -> str:
        """Download photo_url and save it to the cache directory.

        The filename is derived from a hash of the URL for deduplication.

        Args:
            persona_id: Owning persona.
            photo_url: Remote URL to download.

        Returns:
            Absolute path to the cached file (str).
        """
        url_hash = hashlib.sha256(photo_url.encode()).hexdigest()[:16]
        ext = ".jpg" if "jpg" in photo_url else ".png" if "png" in photo_url else ".webp"
        dest = self._cache_dir(persona_id) / f"{url_hash}{ext}"

        if not dest.exists():
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                resp = await client.get(photo_url)
                resp.raise_for_status()
                dest.write_bytes(resp.content)
            logger.info("SeedCache: cached %s → %s", photo_url[:60], dest.name)
        else:
            logger.debug("SeedCache: hit %s", dest.name)

        return str(dest)

    def get_cached_seeds(self, persona_id: str) -> list[str]:
        """Return sorted list of absolute paths to all cached seed images."""
        cache_dir = self._cache_dir(persona_id)
        return sorted(
            str(p) for p in cache_dir.iterdir()
            if p.is_file() and not p.name.startswith(".")
        )

    def is_cache_valid(
        self, persona_id: str, max_age_hours: float = _DEFAULT_MAX_AGE_HOURS
    ) -> bool:
        """Return True if sentinel exists and is younger than max_age_hours."""
        sentinel = self._sentinel_file(persona_id)
        if not sentinel.exists():
            return False
        try:
            ts = datetime.fromisoformat(sentinel.read_text(encoding="utf-8").strip())
            age_hours = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
            return age_hours < max_age_hours
        except Exception:
            return False

    def touch_sentinel(self, persona_id: str) -> None:
        """Write the current UTC timestamp to the sentinel file."""
        sentinel = self._sentinel_file(persona_id)
        sentinel.write_text(
            datetime.now(timezone.utc).isoformat(), encoding="utf-8"
        )
        logger.debug("SeedCache: sentinel touched for persona=%s", persona_id)

    def invalidate_cache(self, persona_id: str) -> None:
        """Remove the sentinel file, marking the cache as stale."""
        sentinel = self._sentinel_file(persona_id)
        if sentinel.exists():
            sentinel.unlink()
        logger.info("SeedCache: invalidated for persona=%s", persona_id)
