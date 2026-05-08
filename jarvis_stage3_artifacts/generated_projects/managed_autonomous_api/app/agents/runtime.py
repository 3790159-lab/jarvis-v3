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


def validate_policy_alignment(agent: AgentRegistryEntry, requested_tools: List[str]) -> Tuple[bool, List[str]]:
    problems = []

    tool_policy_map = {
        "shell": agent.policy.allow_shell,
        "python": agent.policy.allow_python,
        "filesystem": agent.policy.allow_file_write,
        "filesystem_read": agent.policy.allow_file_read,
        "http": agent.policy.allow_http,
        "registry": agent.policy.allow_registry_write
    }

    for tool in requested_tools:
        if tool in tool_policy_map and not tool_policy_map[tool]:
            problems.append(f"Tool blocked by policy: {tool}")

    return (len(problems) == 0, problems)


def preflight_agent_task(agent_id: str, task: AgentTaskEnvelope) -> AgentResultEnvelope:
    agent = resolve_agent(agent_id)

    ok_tools, disallowed_tools = validate_requested_tools(agent, task.requested_tools)
    if not ok_tools:
        return AgentResultEnvelope(
            agent_id=agent.agent_id,
            task_id=task.task_id,
            status="blocked",
            summary="Requested tools are not allowed for this agent",
            errors=[f"Disallowed tools: {', '.join(disallowed_tools)}"],
            requires_human=True,
            confidence=0.99
        )

    ok_policy, policy_problems = validate_policy_alignment(agent, task.requested_tools)
    if not ok_policy:
        return AgentResultEnvelope(
            agent_id=agent.agent_id,
            task_id=task.task_id,
            status="blocked",
            summary="Requested tools violate agent policy",
            errors=policy_problems,
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
