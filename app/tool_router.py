from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RoutePlan:
    route: str
    verified_answer: bool
    tools: list[str] = field(default_factory=list)
    backend: str = "none"
    needs_local_context: bool = False


class ToolRouter:
    def plan(self, intent: str) -> RoutePlan:
        if intent == "trace":
            return RoutePlan(route="trace", verified_answer=True, tools=["trace"], backend="none")
        if intent == "math_exact":
            return RoutePlan(route="math_first", verified_answer=True, tools=["calculator"], backend="none")
        if intent == "current_facts":
            return RoutePlan(route="facts_first", verified_answer=True, tools=["weather_or_web"], backend="none")
        if intent == "photo_vision":
            return RoutePlan(route="vision_first", verified_answer=False, tools=["vision"], backend="openai")
        if intent == "local_project":
            return RoutePlan(route="local_first", verified_answer=False, tools=["local_tools"], backend="ollama", needs_local_context=True)
        if intent == "followup":
            return RoutePlan(route="followup", verified_answer=False, tools=["memory"], backend="openai")
        return RoutePlan(route="general", verified_answer=False, tools=[], backend="openai")
