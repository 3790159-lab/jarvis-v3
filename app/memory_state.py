from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TraceRecord:
    ts: float
    intent: str
    route: str
    tools_used: list[str]
    backend_used: str
    latency_ms: int
    status: str
    note: str = ""


@dataclass
class ChatState:
    last_user_text: str = ""
    last_result: str = ""
    last_result_mode: str = ""
    last_intent: str = ""
    last_route: str = ""
    last_tool_output: dict[str, Any] = field(default_factory=dict)
    last_media: dict[str, Any] = field(default_factory=dict)
    trace_history: list[TraceRecord] = field(default_factory=list)


class MemoryState:
    def __init__(self) -> None:
        self._states: dict[str, ChatState] = {}

    def get(self, chat_id: str) -> ChatState:
        if chat_id not in self._states:
            self._states[chat_id] = ChatState()
        return self._states[chat_id]

    def remember_media(self, chat_id: str, media: dict[str, Any]) -> None:
        state = self.get(chat_id)
        state.last_media = media

    def remember_result(
        self,
        chat_id: str,
        user_text: str,
        result: str,
        *,
        intent: str,
        route: str,
        mode: str,
        tool_output: dict[str, Any] | None = None,
    ) -> None:
        state = self.get(chat_id)
        state.last_user_text = user_text
        state.last_result = result
        state.last_result_mode = mode
        state.last_intent = intent
        state.last_route = route
        state.last_tool_output = tool_output or {}

    def add_trace(
        self,
        chat_id: str,
        *,
        intent: str,
        route: str,
        tools_used: list[str],
        backend_used: str,
        latency_ms: int,
        status: str,
        note: str = "",
    ) -> None:
        state = self.get(chat_id)
        state.trace_history.append(
            TraceRecord(
                ts=time.time(),
                intent=intent,
                route=route,
                tools_used=tools_used,
                backend_used=backend_used,
                latency_ms=latency_ms,
                status=status,
                note=note,
            )
        )
        state.trace_history = state.trace_history[-10:]

    def render_last_trace(self, chat_id: str) -> str:
        state = self.get(chat_id)
        if not state.trace_history:
            return "Ещё нет trace-записей."
        t = state.trace_history[-1]
        tools = ", ".join(t.tools_used) if t.tools_used else "none"
        return (
            "Последний маршрут запроса:\n"
            f"- intent: {t.intent}\n"
            f"- route: {t.route}\n"
            f"- tools: {tools}\n"
            f"- backend: {t.backend_used}\n"
            f"- latency_ms: {t.latency_ms}\n"
            f"- status: {t.status}\n"
            f"- note: {t.note or '—'}"
        )
