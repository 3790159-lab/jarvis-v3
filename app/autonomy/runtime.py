from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from .chains import ChainManager
from .continuation import ContinuationEvaluator, ContinuationExecutor
from .event_bus import EventBus
from .execution_engine import ExecutionEngine
from .execution_graphs import ExecutionGraphManager
from .guards import AutonomyGuardManager
from .maintenance import MaintenanceManager
from .memory_manager import MemoryManager
from .mission_plans import MissionPlanStore
from .mission_registry import MissionRegistry
from .mission_runner import MissionRunner
from .mission_templates import MissionTemplateManager
from .modes import ModeManager
from .operator_escalation import OperatorEscalationManager
from .reconciliation import ReconciliationManager
from .recovery_policy import RecoveryPolicyManager
from .repair_loops import RepairLoopManager
from .replanner import ReplanEngine
from .scheduler import SchedulerService
from .store import JsonStore
from .tools import ToolExecutor, ToolRegistry
from .workflows import WorkflowManager

_AUTONOMY: Dict[str, Any] = {}


def get_autonomy() -> Dict[str, Any]:
    global _AUTONOMY
    if _AUTONOMY:
        return _AUTONOMY

    project_root = Path.cwd()
    store = JsonStore(project_root / "artifacts" / "autonomy")
    event_bus = EventBus(store)
    executor = ContinuationExecutor(store, event_bus)
    evaluator = ContinuationEvaluator(store, event_bus)
    replanner = ReplanEngine(store, event_bus)
    guard_manager = AutonomyGuardManager(store, event_bus)
    memory_manager = MemoryManager(store, event_bus)
    mode_manager = ModeManager(store, event_bus)
    recovery_policy_manager = RecoveryPolicyManager(store, event_bus)
    operator_escalation_manager = OperatorEscalationManager(store, event_bus)
    tool_registry = ToolRegistry()
    mission_registry = MissionRegistry(store, event_bus)

    scheduler = SchedulerService(
        store=store,
        event_bus=event_bus,
        executor=executor,
        evaluator=evaluator,
        replanner=replanner,
        guard_manager=guard_manager,
        memory_manager=memory_manager,
        mode_manager=mode_manager,
    )

    def _autonomy_getter() -> Dict[str, Any]:
        return _AUTONOMY

    tool_executor = ToolExecutor(
        store=store,
        event_bus=event_bus,
        registry=tool_registry,
        autonomy_getter=_autonomy_getter,
    )

    maintenance_manager = MaintenanceManager(
        store=store,
        event_bus=event_bus,
        mode_manager=mode_manager,
    )

    chain_manager = ChainManager(
        tool_executor=tool_executor,
        event_bus=event_bus,
    )

    workflow_manager = WorkflowManager(
        store=store,
        event_bus=event_bus,
        tool_executor=tool_executor,
        chain_manager=chain_manager,
    )

    execution_graph_manager = ExecutionGraphManager(
        store=store,
        event_bus=event_bus,
        tool_executor=tool_executor,
        workflow_manager=workflow_manager,
    )

    repair_loop_manager = RepairLoopManager(
        store=store,
        event_bus=event_bus,
        workflow_manager=workflow_manager,
        maintenance_manager=maintenance_manager,
        autonomy_getter=_autonomy_getter,
    )

    execution_engine = ExecutionEngine(
        store=store,
        event_bus=event_bus,
        tool_executor=tool_executor,
        workflow_manager=workflow_manager,
        graph_manager=execution_graph_manager,
        repair_manager=repair_loop_manager,
        guard_manager=guard_manager,
        mode_manager=mode_manager,
        autonomy_getter=_autonomy_getter,
    )

    mission_plan_store = MissionPlanStore(store=store, event_bus=event_bus)
    mission_template_manager = MissionTemplateManager()

    mission_runner = MissionRunner(
        plan_store=mission_plan_store,
        template_manager=mission_template_manager,
        execution_engine=execution_engine,
        repair_manager=repair_loop_manager,
        guard_manager=guard_manager,
        mode_manager=mode_manager,
        event_bus=event_bus,
        mission_registry=mission_registry,
        recovery_policy_manager=recovery_policy_manager,
        operator_escalation_manager=operator_escalation_manager,
    )

    reconciliation_manager = ReconciliationManager(
        store=store,
        event_bus=event_bus,
        mission_plan_store=mission_plan_store,
    )

    _AUTONOMY = {
        "store": store,
        "event_bus": event_bus,
        "executor": executor,
        "evaluator": evaluator,
        "replanner": replanner,
        "guard_manager": guard_manager,
        "memory_manager": memory_manager,
        "mode_manager": mode_manager,
        "recovery_policy_manager": recovery_policy_manager,
        "operator_escalation_manager": operator_escalation_manager,
        "tool_registry": tool_registry,
        "tool_executor": tool_executor,
        "maintenance_manager": maintenance_manager,
        "mission_registry": mission_registry,
        "chain_manager": chain_manager,
        "workflow_manager": workflow_manager,
        "execution_graph_manager": execution_graph_manager,
        "repair_loop_manager": repair_loop_manager,
        "execution_engine": execution_engine,
        "mission_plan_store": mission_plan_store,
        "mission_template_manager": mission_template_manager,
        "mission_runner": mission_runner,
        "reconciliation_manager": reconciliation_manager,
        "scheduler": scheduler,
    }
    return _AUTONOMY


async def start_autonomy_runtime() -> None:
    autonomy = get_autonomy()
    await autonomy["scheduler"].start()
    autonomy["event_bus"].publish("autonomy_runtime_started", source="runtime")


async def stop_autonomy_runtime() -> None:
    autonomy = get_autonomy()
    await autonomy["scheduler"].stop()
    autonomy["event_bus"].publish("autonomy_runtime_stopped", source="runtime")
