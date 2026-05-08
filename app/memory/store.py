from __future__ import annotations

from typing import Any


class InMemoryStore:
    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def delete(self, key: str) -> None:
        if key in self._data:
            del self._data[key]

    def all(self) -> dict[str, Any]:
        return dict(self._data)


memory_store = InMemoryStore()