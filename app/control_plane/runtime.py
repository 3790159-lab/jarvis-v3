from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .adapters import AdapterFactory
from .applied_learning import AppliedLearningEngine
from .approval import ApprovalManager
from .consultation import ConsultationCoordinator
from .dependency_queue import DependencyQueue
from .dispatcher import Dispatcher
from .integration_task_runtime import IntegrationTaskExecutor
from .lifecycle_manager import LifecycleManager
from .memory import SmartMemoryManager
from .models import MissionSnapshot, TaskRuntimeState, TaskSpec, TaskStatus
from .provider_health import ProviderHealthRegistry
from .qa import QAGate
from .recovery import RecoveryGuardian
from .registry import AgentRegistry
from .snapshot_store import SnapshotStore
from .worker_registry import WorkerRegistry


class SupervisorRuntime:
    def __init__(
        self,
        registry: AgentRegistry,
        dispatcher: Dispatcher,
        consultation_coordinator: ConsultationCoordinator,
        memory: SmartMemoryManager,
        qa_gate: QAGate,
        recovery: RecoveryGuardian,
        adapters: AdapterFactory,
        generated_dir: Path,
        worker_registry: WorkerRegistry,
        dependency_queue: DependencyQueue,
        snapshot_store: SnapshotStore,
        provider_health: ProviderHealthRegistry,
        approval_manager: ApprovalManager,
        runtime_connector_bridge=None,
        integration_task_executor: IntegrationTaskExecutor | None = None,
        applied_learning: AppliedLearningEngine | None = None,
        lifecycle_manager: LifecycleManager | None = None,
        max_parallel_workers: int = 4,
    ) -> None:
        self.registry = registry
        self.dispatcher = dispatcher
        self.consultation_coordinator = consultation_coordinator
        self.memory = memory
        self.qa_gate = qa_gate
        self.recovery = recovery
        self.adapters = adapters
        self.generated_dir = Path(generated_dir)
        self.generated_dir.mkdir(parents=True, exist_ok=True)

        self.worker_registry = worker_registry
        self.dependency_queue = dependency_queue
        self.snapshot_store = snapshot_store
        self.provider_health = provider_health
        self.approval_manager = approval_manager

        self.runtime_connector_bridge = runtime_connector_bridge
        self.integration_task_executor = integration_task_executor or IntegrationTaskExecutor(self.generated_dir)
        self.applied_learning = applied_learning
        self.lifecycle_manager = lifecycle_manager or LifecycleManager(Path("state/agent_mesh"))

        self.max_parallel_workers = max_parallel_workers

    def create_mission(self, goal: str, tasks: list[TaskSpec]) -> MissionSnapshot:
        self.dependency_queue.validate_no_cycles(tasks)
        self.worker_registry.bootstrap_from_registry(self.registry)

        snapshot = MissionSnapshot(
            mission_id=f"mission_{uuid.uuid4().hex[:8]}",
            goal=goal,
            status="pending",
            tasks=[TaskRuntimeState(task=t, status=TaskStatus.PENDING) for t in tasks],
        )
        snapshot.audit_log.append({"event": "mission_created", "goal": goal})
        self.dependency_queue.refresh(snapshot.tasks)
        self._persist(snapshot)
        return snapshot

    def load_mission(self, mission_id: str) -> MissionSnapshot | None:
        if hasattr(self.snapshot_store, "load_any"):
            return self.snapshot_store.load_any(mission_id)
        return self.snapshot_store.load_active(mission_id)

    def run_mission(self, snapshot: MissionSnapshot, context: dict | None = None) -> MissionSnapshot:
        ctx = dict(context or {})
        ctx.setdefault("internet_available", True)
        ctx.setdefault("provider_health", self.provider_health.as_router_context())
        ctx.setdefault("generated_dir", str(self.generated_dir))

        self.worker_registry.bootstrap_from_registry(self.registry)

        safety_counter = 0
        while safety_counter < 60:
            safety_counter += 1
            ctx["provider_health"] = self.provider_health.as_router_context()

            self.dependency_queue.refresh(snapshot.tasks)
            ready = self.dependency_queue.next_ready(snapshot.tasks, max_items=self.max_parallel_workers)
            if not ready:
                break

            bundles = []
            for state in ready:
                dispatch = self.dispatcher.plan(state.task, snapshot, ctx)
                lease = self.worker_registry.claim(
                    capability=state.task.required_capability,
                    preferred_agent_id=dispatch.agent_id,
                )
                if not lease:
                    state.status = TaskStatus.QUEUED
                    self._append_event(snapshot, {
                        "event": "worker_unavailable",
                        "task_id": state.task.task_id,
                        "capability": state.task.required_capability,
                        "preferred_agent": dispatch.agent_id,
                    })
                    continue

                self.worker_registry.assign_task(lease.worker_id, state.task.task_id)
                state.status = TaskStatus.RUNNING
                state.attempts += 1
                state.assigned_agent_id = dispatch.agent_id
                state.provider = dispatch.provider

                if getattr(dispatch, "learning_guidance", None):
                    self._append_event(snapshot, {
                        "event": "learning_guidance_applied",
                        "task_id": state.task.task_id,
                        "agent_id": dispatch.agent_id,
                        "guidance": dispatch.learning_guidance,
                    })

                bundles.append({
                    "state": state,
                    "dispatch": dispatch,
                    "lease": lease,
                    "context": dict(ctx),
                })

            if not bundles:
                break

            with ThreadPoolExecutor(max_workers=self.max_parallel_workers) as executor:
                future_map = {
                    executor.submit(self._execute_bundle, bundle): bundle for bundle in bundles
                }

                for future in as_completed(future_map):
                    bundle = future_map[future]
                    state = bundle["state"]
                    lease = bundle["lease"]
                    dispatch = bundle["dispatch"]

                    try:
                        payload = future.result()
                        latency_ms = payload.get("latency_ms", 0.0)
                        error = payload.get("error")
                        provider_recorded = bool(payload.get("provider_recorded", False))

                        if error:
                            if not provider_recorded:
                                self.provider_health.record_failure(dispatch.provider, error)
                            self._handle_failure(snapshot, state, dispatch.provider, error)
                        else:
                            if not provider_recorded:
                                self.provider_health.record_success(dispatch.provider, latency_ms=latency_ms)
                            self._handle_success(snapshot, state, payload, snapshot.mission_id)
                    except Exception as exc:
                        self.provider_health.record_failure(dispatch.provider, str(exc))
                        self._handle_failure(snapshot, state, dispatch.provider, str(exc))
                    finally:
                        self.worker_registry.release(lease.worker_id)
                        self._persist(snapshot)

        completed = sum(1 for t in snapshot.tasks if t.status == TaskStatus.COMPLETED)
        failed = sum(1 for t in snapshot.tasks if t.status == TaskStatus.FAILED)

        snapshot.status = "completed" if failed == 0 and completed == len(snapshot.tasks) else "partial"
        snapshot.context_bundle = self.memory.build_context_bundle(snapshot)

        self._persist(snapshot)
        self.memory.append_mission_note(
            snapshot,
            body=(
                f"Status: {snapshot.status}\n"
                f"Completed: {completed}\n"
                f"Failed: {failed}\n"
                f"Artifacts: {len(snapshot.artifacts)}\n"
                f"Workers: {self.worker_registry.summary()}\n"
                f"Providers: {self.provider_health.summary()}"
            ),
        )

        try:
            self.lifecycle_manager.on_mission_finished()
        except Exception:
            pass

        return snapshot

    def run_single_task(self, task: TaskSpec, goal: str = "Run one task.", context: dict | None = None) -> MissionSnapshot:
        snapshot = self.create_mission(goal=goal, tasks=[task])
        return self.run_mission(snapshot, context=context or {"internet_available": True})

    def _execute_bundle(self, bundle: dict) -> dict:
        state = bundle["state"]
        dispatch = bundle["dispatch"]
        context = bundle["context"]

        started = time.perf_counter()
        consult_summaries: list[str] = []
        consult_events: list[dict] = []

        for request in dispatch.consultations:
            if state.consult_depth >= state.task.max_consults:
                break

            response = self.consultation_coordinator.resolve(request)
            if response:
                consult_summaries.append(response.summary)
                state.consult_depth += 1
                consult_events.append({
                    "event": "consultation_resolved",
                    "task_id": state.task.task_id,
                    "from_agent": request.from_agent_id,
                    "helper_agent": response.helper_agent_id,
                    "requested_capability": request.requested_capability,
                    "consult_depth": state.consult_depth,
                })

        if state.task.task_type in {"integration", "connector_execution", "service_probe"}:
            if self.runtime_connector_bridge is None:
                return {
                    "result": None,
                    "consult_events": consult_events,
                    "latency_ms": 0.0,
                    "error": "runtime_connector_bridge is not configured",
                    "provider_recorded": False,
                }

            result = self.integration_task_executor.execute(
                agent_id=dispatch.agent_id,
                task=state.task,
                runtime_connector_bridge=self.runtime_connector_bridge,
            )
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            return {
                "result": result,
                "consult_events": consult_events,
                "latency_ms": latency_ms,
                "error": result.error,
                "provider_recorded": True,
            }

        adapter = self.adapters.get(dispatch.agent_id, dispatch.provider)
        result = adapter.invoke(
            agent_id=dispatch.agent_id,
            task=state.task,
            dispatch=dispatch.model_dump(mode="json"),
            context={**context, "consultation_summaries": consult_summaries},
        )

        latency_ms = round((time.perf_counter() - started) * 1000, 2)

        return {
            "result": result,
            "consult_events": consult_events,
            "latency_ms": latency_ms,
            "error": result.error,
            "provider_recorded": False,
        }

    def _handle_success(self, snapshot: MissionSnapshot, state: TaskRuntimeState, payload: dict, mission_id: str) -> None:
        result = payload["result"]
        state.result = result

        for event in payload.get("consult_events", []):
            self._append_event(snapshot, event)

        if result is None:
            self._handle_failure(snapshot, state, state.provider, "Empty result object")
            return

        for proposal in result.proposed_schema_extensions:
            req = self.approval_manager.submit_schema_extension(
                agent_id=result.agent_id,
                task_id=state.task.task_id,
                payload=proposal.model_dump(mode="json"),
                reason=proposal.reason,
                auto_approve=(proposal.scope == "mission"),
            )
            if req.status.value in {"approved", "auto_approved"}:
                state.result.approved_schema_extensions.append(proposal.field_name)
                self._append_event(snapshot, {
                    "event": "schema_extension_approved",
                    "agent_id": result.agent_id,
                    "task_id": state.task.task_id,
                    "field_name": proposal.field_name,
                    "approval_id": req.approval_id,
                })
            else:
                self._append_event(snapshot, {
                    "event": "schema_extension_pending",
                    "agent_id": result.agent_id,
                    "task_id": state.task.task_id,
                    "field_name": proposal.field_name,
                    "approval_id": req.approval_id,
                })

        proposed = str(result.dynamic_fields.get("proposed_capability") or "").strip()
        if proposed:
            self.registry.propose_capability(result.agent_id, proposed)
            req = self.approval_manager.submit_dynamic_capability(
                agent_id=result.agent_id,
                task_id=state.task.task_id,
                capability=proposed,
                reason="Agent proposed dynamic capability expansion.",
                auto_approve=bool(state.task.metadata.get("auto_approve_capability")),
            )
            self._append_event(snapshot, {
                "event": "capability_proposed",
                "agent_id": result.agent_id,
                "capability": proposed,
                "approval_id": req.approval_id,
            })
            if req.status.value in {"approved", "auto_approved"}:
                self.registry.approve_capability(result.agent_id, proposed)
                self._append_event(snapshot, {
                    "event": "capability_approved",
                    "agent_id": result.agent_id,
                    "capability": proposed,
                    "approval_id": req.approval_id,
                })

        qa = self.qa_gate.evaluate(state.task, result)
        state.qa = qa.model_dump(mode="json")

        if self.applied_learning is not None:
            learn_info = self.applied_learning.learn_from_result(
                task=state.task,
                result=result,
                qa_dict=state.qa,
                mission_id=mission_id,
                provider=state.provider,
                passed=qa.passed,
            )
            self._append_event(snapshot, {
                "event": "learning_recorded",
                "task_id": state.task.task_id,
                "agent_id": result.agent_id,
                "learning": learn_info,
            })

        if qa.passed:
            state.status = TaskStatus.COMPLETED
            state.recovery = None
            snapshot.artifacts.extend(result.artifacts)
            self._append_event(snapshot, {
                "event": "task_completed",
                "task_id": state.task.task_id,
                "agent_id": result.agent_id,
                "provider": state.provider,
                "qa_summary": qa.summary,
            })
        else:
            self._handle_failure(snapshot, state, state.provider, qa.summary)

    def _handle_failure(self, snapshot: MissionSnapshot, state: TaskRuntimeState, provider: str, error_text: str) -> None:
        plan = self.recovery.plan(state, error_text)
        state.recovery = plan.model_dump(mode="json")

        if plan.fallback_provider:
            state.task.metadata["forced_provider"] = plan.fallback_provider

        if plan.requires_approval:
            state.status = TaskStatus.WAITING_APPROVAL
        elif plan.retryable and state.attempts < state.task.max_retries:
            state.status = TaskStatus.RETRY_SCHEDULED
        else:
            state.status = TaskStatus.FAILED

        self._append_event(snapshot, {
            "event": "task_recovery_planned",
            "task_id": state.task.task_id,
            "provider": provider,
            "reason": plan.reason,
            "action": plan.action,
            "fallback_provider": plan.fallback_provider,
            "escalation_level": plan.escalation_level,
            "requires_approval": plan.requires_approval,
            "error": error_text,
        })

    def _persist(self, snapshot: MissionSnapshot) -> None:
        self.memory.persist_snapshot(snapshot)
        self.snapshot_store.persist_active(snapshot)

    def _append_event(self, snapshot: MissionSnapshot, event: dict) -> None:
        snapshot.audit_log.append(event)
        self.snapshot_store.append_journal(snapshot.mission_id, event)


def build_demo_tasks() -> list[TaskSpec]:
    return [
        TaskSpec(
            task_id="plan_goal",
            title="Plan multi-agent mission",
            task_type="planning",
            required_capability="plan",
            priority="high",
            consultation_allowed=False,
            metadata={"allow_schema_extension": True},
        ),
        TaskSpec(
            task_id="research_context",
            title="Gather supporting context",
            task_type="research",
            required_capability="analyze",
            depends_on=["plan_goal"],
            priority="normal",
            consultation_allowed=True,
            metadata={"needs_memory_support": True},
        ),
        TaskSpec(
            task_id="generate_artifact",
            title="Generate demo control-plane artifact",
            task_type="codegen",
            required_capability="codegen",
            depends_on=["plan_goal"],
            priority="high",
            risk_level="high",
            consultation_allowed=True,
            metadata={
                "needs_research": True,
                "needs_memory_support": True,
                "proposed_capability": "patch_review",
                "auto_approve_capability": True,
            },
        ),
        TaskSpec(
            task_id="qa_review",
            title="Run QA validation",
            task_type="validation",
            required_capability="validate",
            depends_on=["research_context", "generate_artifact"],
            priority="high",
            consultation_allowed=False,
        ),
        TaskSpec(
            task_id="integration_claude_check",
            title="Run native Claude connector check",
            task_type="integration",
            required_capability="codegen",
            depends_on=["qa_review"],
            priority="high",
            consultation_allowed=False,
            metadata={
                "preferred_service": "claude_bridge",
                "connector_action": "config_check",
                "dry_run": False,
                "persist_output": True,
            },
        ),
        TaskSpec(
            task_id="integration_n8n_dryrun",
            title="Run native n8n dry-run handoff",
            task_type="integration",
            required_capability="workflow_run",
            depends_on=["qa_review"],
            priority="normal",
            consultation_allowed=False,
            metadata={
                "preferred_service": "n8n",
                "connector_action": "webhook_invoke",
                "dry_run": True,
                "persist_output": True,
                "connector_payload": {
                    "webhook_path": "demo-workflow",
                    "body": {
                        "ping": True,
                        "source": "native_integration_demo",
                    },
                },
            },
        ),
        TaskSpec(
            task_id="memory_commit",
            title="Persist memory bundle",
            task_type="memory_write",
            required_capability="memory_write",
            depends_on=["qa_review", "integration_claude_check", "integration_n8n_dryrun"],
            priority="normal",
            locality_requirement=True,
            storage_affinity=True,
            consultation_allowed=False,
        ),
    ]