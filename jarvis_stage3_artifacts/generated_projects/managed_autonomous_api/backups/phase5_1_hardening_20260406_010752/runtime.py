from __future__ import annotations

from typing import List, Tuple

from app.agents.registry import AgentRegistry
from app.agents.schemas import AgentRegistryEntry, AgentResultEnvelope, AgentTaskEnvelope


def resolve_agent(agent_id: str) -> AgentRegistryEntry:
    registry = AgentRegistry()
    agent = registry.get(agent_id)
    if not agent:
        raise ValueError(f"Unknown agent_id: {agent_id}")
    if agent.status != "active":
        raise ValueError(f"Agent is not active: {agent_id} ({agent.status})")
    return agent


def validate_requested_tools(agent: AgentRegistryEntry, requested_tools: List[str]) -> Tuple[bool, List[str]]:
    disallowed = [tool for tool in requested_tools if tool not in agent.allowed_tools]
    return (len(disallowed) == 0, disallowed)


def preflight_agent_task(agent_id: str, task: AgentTaskEnvelope) -> AgentResultEnvelope:
    agent = resolve_agent(agent_id)
    ok, disallowed = validate_requested_tools(agent, task.requested_tools)

    if not ok:
        return AgentResultEnvelope(
            agent_id=agent.agent_id,
            task_id=task.task_id,
            status="blocked",
            summary="Requested tools are not allowed for this agent",
            errors=[f"Disallowed tools: {', '.join(disallowed)}"],
            requires_human=True,
            confidence=0.99
        )

    if len(task.objective.strip()) == 0:
        return AgentResultEnvelope(
            agent_id=agent.agent_id,
            task_id=task.task_id,
            status="failed",
            summary="Objective is empty",
            errors=["Task objective must not be empty"],
            requires_human=False,
            confidence=1.0
        )

    return AgentResultEnvelope(
        agent_id=agent.agent_id,
        task_id=task.task_id,
        status="completed",
        summary="Agent task preflight passed",
        artifacts=[],
        errors=[],
        next_action="execution_allowed",
        requires_human=False,
        confidence=0.9,
        output_payload={
            "agent_role": agent.role,
            "policy_profile": agent.policy.profile,
            "allowed_tools": agent.allowed_tools,
            "requested_tools": task.requested_tools
        }
    )
