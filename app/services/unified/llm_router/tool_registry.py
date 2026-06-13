# -*- coding: utf-8 -*-
"""Tool registry for the unified LLM router.

A :class:`Tool` couples an Anthropic tool *definition* (name + Russian
description + JSON-schema inputs) with an async *handler* the router invokes
when Claude calls it. The :class:`ToolRegistry` holds the set of tools offered
on each request and renders them into the ``tools`` payload the Messages API
expects.

:class:`ToolContext` carries the per-request identity the handlers need
(user/chat ids, mutable conversation state). :class:`ToolResult` is the
uniform return type — text, photo, video, or error — that the router both
feeds back to Claude (as a ``tool_result`` block) and surfaces to the caller
(for media rendering in Telegram).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional


@dataclass
class ToolContext:
    """Per-request identity + scratch state passed to every tool handler."""

    user_id: Optional[int]
    username: Optional[str]
    chat_id: str
    conversation_state: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    """Uniform tool outcome: text, photo, video, or error.

    ``kind`` is one of ``"text" | "photo" | "video" | "error"``. For media
    results, ``media`` holds a URL or local path and ``text`` is an optional
    caption. :meth:`to_tool_content` renders the string the router sends back
    to Claude as the ``tool_result`` content.
    """

    kind: str
    text: str = ""
    media: Optional[str] = None
    error: str = ""

    # ── constructors ─────────────────────────────────────────────────────────
    @classmethod
    def ok_text(cls, text: str) -> "ToolResult":
        return cls(kind="text", text=text)

    @classmethod
    def photo(cls, media: str, caption: str = "") -> "ToolResult":
        return cls(kind="photo", media=media, text=caption)

    @classmethod
    def video(cls, media: str, caption: str = "") -> "ToolResult":
        return cls(kind="video", media=media, text=caption)

    @classmethod
    def fail(cls, error: str) -> "ToolResult":
        return cls(kind="error", error=error)

    # ── helpers ───────────────────────────────────────────────────────────────
    @property
    def is_error(self) -> bool:
        return self.kind == "error"

    @property
    def is_media(self) -> bool:
        return self.kind in ("photo", "video")

    def to_tool_content(self) -> str:
        """Render the string fed back to Claude as ``tool_result`` content."""
        if self.is_error:
            return f"ERROR: {self.error}"
        if self.is_media:
            label = "Фото" if self.kind == "photo" else "Видео"
            caption = f" — {self.text}" if self.text else ""
            return f"[{label} отправлено пользователю: {self.media}]{caption}"
        return self.text


# A handler is ``async (params, context) -> ToolResult``.
ToolHandler = Callable[[Dict[str, Any], ToolContext], Awaitable[ToolResult]]


@dataclass
class Tool:
    """A registered tool: Anthropic definition + async handler."""

    name: str
    description: str
    input_schema: Dict[str, Any]
    handler: ToolHandler

    def to_anthropic(self) -> Dict[str, Any]:
        """Render the tool definition block for the Messages API ``tools`` list."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


class ToolRegistry:
    """An ordered collection of :class:`Tool` instances keyed by name."""

    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> Tool:
        """Add ``tool`` (replacing any existing tool with the same name)."""
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def names(self) -> List[str]:
        return list(self._tools.keys())

    def all(self) -> List[Tool]:
        return list(self._tools.values())

    def to_anthropic_tools(self) -> List[Dict[str, Any]]:
        """Render every registered tool into the Messages API ``tools`` payload."""
        return [t.to_anthropic() for t in self._tools.values()]

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)


# Process-wide default registry (the singleton the bot wires its tools into).
_DEFAULT_REGISTRY: Optional[ToolRegistry] = None


def get_default_registry() -> ToolRegistry:
    """Return the lazily-created process-wide default :class:`ToolRegistry`."""
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = ToolRegistry()
    return _DEFAULT_REGISTRY
