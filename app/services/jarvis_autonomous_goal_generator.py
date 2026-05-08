from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class AutonomousGoal:
    goal_id: str
    title: str
    task: str
    priority: int
    risk: str
    lane_hint: str
    strategy_area: str
    success_criteria: List[str]
    created_at: str = field(default_factory=utc_now)


class JarvisAutonomousGoalGenerator:
    """
    Autonomous Goal Generator v1.

    Purpose:
    - keep a long-term strategy
    - generate prioritized low/medium-risk tasks
    - avoid doing random improvements forever
    - feed Night Mode with strategy-aligned tasks
    """

    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root).resolve()
        self.root = self.project_root / "jarvis_stage3_artifacts" / "autonomous_goal_generator"
        self.root.mkdir(parents=True, exist_ok=True)

        self.strategy_path = self.root / "long_term_strategy.json"
        self.queue_path = self.root / "goal_queue.json"
        self.events_path = self.root / "goal_events.jsonl"

        self._ensure_strategy()

    def _read_json(self, path: Path, default: Any) -> Any:
        try:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
        return default

    def _write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    def _append_event(self, event: Dict[str, Any]) -> None:
        with self.events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    def _ensure_strategy(self) -> None:
        if self.strategy_path.exists():
            return

        strategy = {
            "version": "1.0",
            "created_at": utc_now(),
            "mission": "Build Jarvis into a reliable autonomous AI operator that can understand goals, plan tasks, use tools, verify real outcomes, learn from results, and safely improve itself.",
            "principles": [
                "Truth over confidence: never claim external completion without real evidence.",
                "Safety first: high-risk changes require approval or plan-only mode.",
                "Small reversible improvements beat large fragile rewrites.",
                "Every execution should produce artifacts, validation, and lessons.",
                "Telegram UX should be clear, proactive, and non-spammy.",
                "n8n workflows should become real multi-service automations, not echo demos.",
                "Google/Gmail/Calendar integrations must prove real API result before claiming success."
            ],
            "strategic_areas": [
                {
                    "name": "truth_and_verification",
                    "priority": 100,
                    "description": "Prevent hallucinated completion. Require evidence for Google Sheets, n8n, files, Gmail, Calendar.",
                },
                {
                    "name": "autonomous_planning",
                    "priority": 95,
                    "description": "Generate, prioritize and execute follow-up goals without waiting for the operator.",
                },
                {
                    "name": "execution_lanes",
                    "priority": 90,
                    "description": "Expand Brain Executor beyond n8n/code lane into Google Sheets, Gmail, Calendar, Telegram and HTTP.",
                },
                {
                    "name": "memory_learning",
                    "priority": 85,
                    "description": "Store lessons and use them to avoid repeating mistakes.",
                },
                {
                    "name": "telegram_operator_ux",
                    "priority": 80,
                    "description": "Answer clearly, notify completion, explain what was really done, and support natural task queries.",
                },
                {
                    "name": "n8n_pipeline_intelligence",
                    "priority": 75,
                    "description": "Build dynamic multi-service workflows with validation, branching and test results.",
                },
                {
                    "name": "observability_and_recovery",
                    "priority": 70,
                    "description": "Improve logs, state snapshots, rollback artifacts and safe recovery.",
                }
            ],
            "current_focus": [
                "truth_and_verification",
                "autonomous_planning",
                "execution_lanes"
            ],
            "definition_of_done": [
                "Task has a real result or an honest blocked reason.",
                "Result is validated.",
                "Artifact is saved.",
                "Operator receives a concise completion message.",
                "Lesson is stored if anything failed or was fixed."
            ]
        }
        self._write_json(self.strategy_path, strategy)

    def load_strategy(self) -> Dict[str, Any]:
        self._ensure_strategy()
        return self._read_json(self.strategy_path, {})

    def load_queue(self) -> List[Dict[str, Any]]:
        return self._read_json(self.queue_path, [])

    def save_queue(self, queue: List[Dict[str, Any]]) -> None:
        # keep bounded and sorted by priority desc
        queue = sorted(queue, key=lambda x: int(x.get("priority", 0)), reverse=True)
        self._write_json(self.queue_path, queue[:100])

    def _recent_event_text(self, limit: int = 20) -> str:
        candidates = []

        roots = [
            self.project_root / "jarvis_stage3_artifacts" / "brain_executor" / "runtime" / "latest_execution.json",
            self.project_root / "jarvis_stage3_artifacts" / "telegram_task_watcher" / "watcher_meta_v2.json",
            self.project_root / "jarvis_stage3_artifacts" / "night_v6_2_brain_safe",
            self.project_root / "jarvis_stage3_artifacts" / "memory_learning" / "code_improvement_lessons.jsonl",
        ]

        for p in roots:
            try:
                if p.is_file():
                    candidates.append(p.read_text(encoding="utf-8", errors="replace")[:4000])
                elif p.is_dir():
                    files = sorted(p.rglob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:5]
                    for f in files:
                        candidates.append(f.read_text(encoding="utf-8", errors="replace")[:2000])
            except Exception:
                pass

        return "\n\n".join(candidates)[:12000]

    def _goal(self, title: str, task: str, priority: int, risk: str, lane_hint: str, strategy_area: str, success: List[str]) -> AutonomousGoal:
        return AutonomousGoal(
            goal_id="goal_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8],
            title=title,
            task=task,
            priority=priority,
            risk=risk,
            lane_hint=lane_hint,
            strategy_area=strategy_area,
            success_criteria=success,
        )

    def generate_goals(self, count: int = 5, reason: str = "scheduled") -> List[AutonomousGoal]:
        strategy = self.load_strategy()
        recent = self._recent_event_text()

        goals: List[AutonomousGoal] = []

        # Always keep truth/evidence work high priority until stable.
        goals.append(self._goal(
            title="Verify truth guard coverage",
            task=(
                "Autonomous strategic goal: audit Truth Guard and completion watcher coverage. "
                "Verify Google Sheets, n8n, code improvement, file artifact and generic tasks. "
                "If gaps are found, create a safe fix with smoke tests."
            ),
            priority=100,
            risk="low",
            lane_hint="code_improvement_lane",
            strategy_area="truth_and_verification",
            success=[
                "Truth Guard smoke passes.",
                "Fake Google Sheets task is rejected without spreadsheet_url.",
                "Real n8n workflow result is accepted.",
            ],
        ))

        goals.append(self._goal(
            title="Expand execution lanes",
            task=(
                "Autonomous strategic goal: inspect Brain Executor lanes and add/plan the next safest execution lane. "
                "Prioritize Google Sheets real executor, Telegram notifier, HTTP validation, and file artifact creation. "
                "Keep changes reversible and compile-checked."
            ),
            priority=95,
            risk="medium",
            lane_hint="code_improvement_lane",
            strategy_area="execution_lanes",
            success=[
                "Brain Executor has a clearer next lane plan or safe implementation.",
                "Compile checks pass.",
                "Rollback artifact exists.",
            ],
        ))

        goals.append(self._goal(
            title="Improve autonomous task generation",
            task=(
                "Autonomous strategic goal: improve goal generation so Jarvis creates useful follow-up tasks after every run, "
                "deduplicates repeated goals, scores priority/risk, and avoids looping on the same task forever."
            ),
            priority=94,
            risk="low",
            lane_hint="code_improvement_lane",
            strategy_area="autonomous_planning",
            success=[
                "Goal queue contains prioritized non-duplicate tasks.",
                "Night Mode can consume generated goals.",
                "Seed events are logged.",
            ],
        ))

        goals.append(self._goal(
            title="Improve memory learning usefulness",
            task=(
                "Autonomous strategic goal: summarize recent execution lessons into actionable improvements. "
                "Detect repeated failures, repeated completed-only runs, and missing evidence patterns."
            ),
            priority=88,
            risk="low",
            lane_hint="code_improvement_lane",
            strategy_area="memory_learning",
            success=[
                "Learning summary artifact exists.",
                "Repeated issues are identified.",
                "Next tasks are proposed from lessons.",
            ],
        ))

        goals.append(self._goal(
            title="Improve Telegram operator experience",
            task=(
                "Autonomous strategic goal: improve Telegram UX so Jarvis answers status questions clearly, "
                "does not hallucinate links, finds tasks by fuzzy text, and sends concise completion messages without spam."
            ),
            priority=84,
            risk="low",
            lane_hint="code_improvement_lane",
            strategy_area="telegram_operator_ux",
            success=[
                "Completion watcher remains non-spammy.",
                "Truth-guarded wording is used.",
                "Task lookup UX is improved or planned.",
            ],
        ))

        goals.append(self._goal(
            title="Improve n8n dynamic pipeline intelligence",
            task=(
                "Autonomous strategic goal: inspect latest n8n workflow artifacts and improve dynamic pipeline templates. "
                "Avoid fallback Echo workflow for complex automation requests. Prefer validate -> action -> transform -> report."
            ),
            priority=78,
            risk="medium",
            lane_hint="n8n_super_agent",
            strategy_area="n8n_pipeline_intelligence",
            success=[
                "Dynamic pipeline does not degrade into Echo for complex requests.",
                "Workflow result includes workflow_id and status.",
                "Webhook test passes when created.",
            ],
        ))

        # Context-sensitive goals from recent text
        r = recent.lower()
        if "compiled_only" in r:
            goals.append(self._goal(
                title="Reduce compiled_only outcomes",
                task="Autonomous strategic goal: inspect why recent tasks ended as compiled_only and add a safe execution adapter or clearer blocked reason.",
                priority=92,
                risk="medium",
                lane_hint="code_improvement_lane",
                strategy_area="execution_lanes",
                success=["Fewer compiled_only outcomes.", "Missing lane is identified.", "Safe adapter plan exists."],
            ))

        if "spreadsheet" in r or "google" in r or "таблиц" in r:
            goals.append(self._goal(
                title="Build real Google Sheets lane",
                task="Autonomous strategic goal: design or implement real Google Sheets execution lane that returns spreadsheet_id and spreadsheet_url before claiming success.",
                priority=96,
                risk="medium",
                lane_hint="code_improvement_lane",
                strategy_area="execution_lanes",
                success=["Google Sheets lane requires real API confirmation.", "Truth Guard accepts only real sheet URLs.", "No fake links are generated."],
            ))

        if "error" in r or "failed" in r or "traceback" in r:
            goals.append(self._goal(
                title="Audit recurring failures",
                task="Autonomous strategic goal: audit latest errors/tracebacks, classify root cause, create rollback-safe fix, and add a smoke test.",
                priority=98,
                risk="low",
                lane_hint="code_improvement_lane",
                strategy_area="observability_and_recovery",
                success=["Latest errors summarized.", "Root cause identified.", "Smoke test added or updated."],
            ))

        # Deduplicate titles against current queue
        queue = self.load_queue()
        existing_titles = {x.get("title") for x in queue}
        fresh = [g for g in goals if g.title not in existing_titles]

        selected = sorted(fresh, key=lambda g: g.priority, reverse=True)[:count]

        queue.extend([asdict(g) for g in selected])
        self.save_queue(queue)

        self._append_event({
            "created_at": utc_now(),
            "reason": reason,
            "generated_count": len(selected),
            "goals": [asdict(g) for g in selected],
            "strategy_focus": strategy.get("current_focus", []),
        })

        return selected

    def next_goal_task(self, fallback: str) -> str:
        queue = self.load_queue()

        if not queue:
            self.generate_goals(count=5, reason="queue_empty")
            queue = self.load_queue()

        if not queue:
            return fallback

        goal = queue.pop(0)
        self.save_queue(queue)

        self._append_event({
            "created_at": utc_now(),
            "event": "goal_consumed",
            "goal": goal,
        })

        return goal.get("task") or fallback

    def status(self) -> Dict[str, Any]:
        strategy = self.load_strategy()
        queue = self.load_queue()
        return {
            "strategy_version": strategy.get("version"),
            "mission": strategy.get("mission"),
            "current_focus": strategy.get("current_focus"),
            "queue_size": len(queue),
            "top_goals": queue[:10],
        }