from __future__ import annotations

import asyncio


class MissionLockManager:
    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    def get_lock(self, mission_id: str) -> asyncio.Lock:
        if mission_id not in self._locks:
            self._locks[mission_id] = asyncio.Lock()
        return self._locks[mission_id]


mission_lock_manager = MissionLockManager()
