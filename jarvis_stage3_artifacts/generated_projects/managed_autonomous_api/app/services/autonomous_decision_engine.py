from __future__ import annotations

import json
import threading
import uuid
import urllib.error
import urllib.parse
import urllib.request
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AutonomousDecisionEngine:
    MAX_RUNS = 500
    MAX_EVENTS_PER_RUN = 200
    LOOP_MAX_STEPS_DEFAULT = 5

    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.store_path = self.base_dir / "decision_runs.json"
        self._lock = threading.RLock()

    def _default_store(self) -> Dict[str, Any]:
        return {
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "runs": []
        }

    def _read_store(self) -> Dict[str, Any]:
        if not self.store_path.exists():
            store = self._default_store()
            self._write_store(store)
            return store

        try:
            data = json.loads(self.store_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Store must be a JSON object")
            if "runs" not in data or not isinstance(data["runs"], list):
                data["runs"] = []
            if "created_at" not in data:
                data["created_at"] = utc_now()
            if "updated_at" not in data:
                data["updated_at"] = utc_now()

            changed = False
            for run in data["runs"]:
                if "execution_result" not in run:
                    run["execution_result"] = None
                    changed = True
                if "loop" not in run:
                    run["loop"] = {
                        "enabled": False,
                        "step_count": 0,
                        "max_steps": self.LOOP_MAX_STEPS_DEFAULT,
                        "history": [],
                        "final_outcome": None,
                    }
                    changed = True
                if "status" not in run:
                    run["status"] = "ready"
                    changed = True

            if changed:
                self._write_store(data)

            return data
        except Exception:
            store = self._default_store()
            self._write_store(store)
            return store

    def _write_store(self, store: Dict[str, Any]) -> None:
        store["updated_at"] = utc_now()
        tmp = self.store_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.store_path)

    def _append_event(self, run: Dict[str, Any], message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        item = {
            "ts": utc_now(),
            "message": message,
        }
        if extra:
            item["extra"] = extra
        run["events"].append(item)
        if len(run["events"]) > self.MAX_EVENTS_PER_RUN:
            run["events"] = run["events"][-self.MAX_EVENTS_PER_RUN:]

    def _ensure_loop(self, run: Dict[str, Any]) -> None:
        if "loop" not in run or not isinstance(run["loop"], dict):
            run["loop"] = {
                "enabled": False,
                "step_count": 0,
                "max_steps": self.LOOP_MAX_STEPS_DEFAULT,
                "history": [],
                "final_outcome": None,
            }
        if "history" not in run["loop"] or not isinstance(run["loop"]["history"], list):
            run["loop"]["history"] = []
        if "step_count" not in run["loop"]:
            run["loop"]["step_count"] = 0
        if "max_steps" not in run["loop"]:
            run["loop"]["max_steps"] = self.LOOP_MAX_STEPS_DEFAULT
        if "final_outcome" not in run["loop"]:
            run["loop"]["final_outcome"] = None

    def _find_run_ref(self, store: Dict[str, Any], decision_id: str) -> Optional[Dict[str, Any]]:
        for item in store["runs"]:
            if item.get("decision_id") == decision_id:
                return item
        return None

    def health(self) -> Dict[str, Any]:
        with self._lock:
            store = self._read_store()
            runs = store["runs"]
            legacy_ready = len([r for r in runs if r.get("status") == "ready" and not r.get("execution_result")])
            loop_enabled = len([r for r in runs if isinstance(r.get("loop"), dict) and r["loop"].get("enabled")])
            return {
                "status": "healthy",
                "store_path": str(self.store_path),
                "runs_count": len(runs),
                "legacy_ready_count": legacy_ready,
                "loop_enabled_count": loop_enabled,
                "updated_at": store["updated_at"],
            }

    def reconcile_legacy_runs(self) -> Dict[str, Any]:
        with self._lock:
            store = self._read_store()
            changed = 0
            for run in store["runs"]:
                self._ensure_loop(run)
                if run.get("status") == "ready" and not run.get("execution_result"):
                    run["status"] = "legacy_unexecuted"
                    run["updated_at"] = utc_now()
                    self._append_event(run, "Legacy run reconciled", {"new_status": "legacy_unexecuted"})
                    changed += 1
            if changed:
                self._write_store(store)
            return {
                "status": "ok",
                "reconciled_count": changed,
                "runs_count": len(store["runs"]),
            }

    def _compute_next_action(
        self,
        goal: str,
        risk_level: str,
        has_memory: bool,
        requires_tools: bool,
        requires_approval: bool,
        mission_type: str,
    ) -> Dict[str, Any]:
        goal_l = (goal or "").lower()
        risk = (risk_level or "medium").strip().lower()

        if requires_approval or risk in {"high", "critical"}:
            return {
                "action": "request_approval",
                "reason": "Goal is marked as high-risk or explicitly requires approval",
                "target_layer": "hitl",
                "confidence": 0.96,
            }

        if has_memory and mission_type:
            return {
                "action": "retrieve_memory_guidance",
                "reason": "Mission type has reusable history and should consult memory first",
                "target_layer": "mission_memory_v2",
                "confidence": 0.87,
            }

        if requires_tools:
            return {
                "action": "plan_tool_execution",
                "reason": "Goal requires controlled tool usage before mission execution",
                "target_layer": "tools",
                "confidence": 0.90,
            }

        if "mission" in goal_l or "execute" in goal_l or "run" in goal_l:
            return {
                "action": "start_mission_execution",
                "reason": "Goal looks execution-oriented and can enter mission runner",
                "target_layer": "execution",
                "confidence": 0.88,
            }

        return {
            "action": "build_plan_only",
            "reason": "Goal should be decomposed before risky or tool-driven execution",
            "target_layer": "planner",
            "confidence": 0.80,
        }

    def decide(
        self,
        goal: str,
        mission_type: str = "",
        risk_level: str = "medium",
        has_memory: bool = True,
        requires_tools: bool = False,
        requires_approval: bool = False,
        context: Optional[Dict[str, Any]] = None,
        loop_enabled: bool = False,
        loop_max_steps: int = 5,
    ) -> Dict[str, Any]:
        with self._lock:
            store = self._read_store()

            decision = self._compute_next_action(
                goal=goal,
                risk_level=risk_level,
                has_memory=has_memory,
                requires_tools=requires_tools,
                requires_approval=requires_approval,
                mission_type=mission_type,
            )

            run = {
                "decision_id": f"decision_{uuid.uuid4().hex[:10]}",
                "goal": goal,
                "mission_type": mission_type,
                "risk_level": risk_level,
                "has_memory": bool(has_memory),
                "requires_tools": bool(requires_tools),
                "requires_approval": bool(requires_approval),
                "context": context or {},
                "decision": decision,
                "status": "ready",
                "created_at": utc_now(),
                "updated_at": utc_now(),
                "execution_result": None,
                "events": [],
                "loop": {
                    "enabled": bool(loop_enabled),
                    "step_count": 0,
                    "max_steps": max(1, int(loop_max_steps)),
                    "history": [],
                    "final_outcome": None,
                },
            }

            self._append_event(run, "Decision created", {"action": decision["action"]})
            self._append_event(run, "Target layer selected", {"target_layer": decision["target_layer"]})

            store["runs"].append(run)
            if len(store["runs"]) > self.MAX_RUNS:
                store["runs"] = store["runs"][-self.MAX_RUNS:]

            self._write_store(store)
            return deepcopy(run)

    def get_run(self, decision_id: str) -> Dict[str, Any]:
        with self._lock:
            store = self._read_store()
            run = self._find_run_ref(store, decision_id)
            if run:
                return deepcopy(run)
            return {
                "found": False,
                "decision_id": decision_id,
                "message": "Decision run not found",
            }

    def list_runs(self) -> Dict[str, Any]:
        with self._lock:
            store = self._read_store()
            items: List[Dict[str, Any]] = deepcopy(store["runs"])
            items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
            return {
                "count": len(items),
                "items": items,
            }

    def _http_json(self, method: str, url: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        data = None
        headers = {"Content-Type": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = resp.read().decode("utf-8", errors="replace")
            try:
                return json.loads(payload)
            except Exception:
                return {"raw": payload}

    def _bridge_once(self, run: Dict[str, Any], base_url: str) -> Dict[str, Any]:
        decision = run["decision"]
        action = decision["action"]

        if action == "retrieve_memory_guidance":
            mission_type = urllib.parse.quote(run.get("mission_type", ""))
            url = f"{base_url}/api/mission-memory-v2/suggest/{mission_type}"
            result = self._http_json("GET", url)
            return {"status": "completed", "result": result}

        if action == "request_approval":
            url = f"{base_url}/api/hitl/approvals"
            approval_body = {
                "mission_id": run["context"].get("mission_id", run["decision_id"]),
                "action_type": run["context"].get("action_type", "autonomous_action"),
                "proposed_action": run["goal"],
                "reason": decision["reason"],
                "risk_level": run.get("risk_level", "medium"),
                "agent_id": run["context"].get("agent_id", "autonomous_decision_engine"),
                "agent_score": float(run["context"].get("agent_score", 0.0)),
                "payload": run.get("context", {}),
            }
            result = self._http_json("POST", url, approval_body)
            return {"status": "blocked", "result": result}

        if action == "plan_tool_execution":
            target_path = run["context"].get("target_path", "artifacts/output/autonomous_demo.txt")
            content = run["context"].get("content", f"Autonomous decision output for: {run['goal']}")
            url = f"{base_url}/api/tools/filesystem/write"
            result = self._http_json("POST", url, {
                "path": target_path,
                "content": content,
            })
            return {"status": "completed" if result.get("ok") else "failed", "result": result}

        if action == "start_mission_execution":
            mission_id = run["context"].get("mission_id", f"mission_{run['decision_id']}")
            url = f"{base_url}/api/execution/missions/{urllib.parse.quote(mission_id)}/run"
            result = self._http_json("POST", url)
            return {"status": "completed", "result": result}

        return {
            "status": "completed",
            "result": {
                "message": "No direct execution bridge for this action yet",
                "action": action,
                "target_layer": decision.get("target_layer"),
            }
        }

    def execute_now(self, decision_id: str, base_url: str = "http://127.0.0.1:8010") -> Dict[str, Any]:
        with self._lock:
            store = self._read_store()
            run = self._find_run_ref(store, decision_id)
            if not run:
                return {
                    "found": False,
                    "decision_id": decision_id,
                    "message": "Decision run not found",
                }

            self._ensure_loop(run)

            if run.get("status") == "completed" and not run["loop"].get("enabled"):
                self._append_event(run, "Execute-now ignored because non-loop run is already completed")
                self._write_store(store)
                return deepcopy(run)

            run["status"] = "executing"
            run["updated_at"] = utc_now()
            self._append_event(run, "Execution bridge started", {"loop_enabled": run["loop"]["enabled"]})
            self._write_store(store)

        try:
            with self._lock:
                store = self._read_store()
                run = self._find_run_ref(store, decision_id)
                if not run:
                    return {"found": False, "decision_id": decision_id, "message": "Decision run disappeared"}
                self._ensure_loop(run)
                loop_enabled = bool(run["loop"]["enabled"])
                max_steps = int(run["loop"]["max_steps"])

            if not loop_enabled:
                step = self._bridge_once(run, base_url)
                final_status = step["status"]
                result = step["result"]

                with self._lock:
                    store = self._read_store()
                    run = self._find_run_ref(store, decision_id)
                    if not run:
                        return {"found": False, "decision_id": decision_id, "message": "Decision run disappeared"}
                    self._ensure_loop(run)
                    run["execution_result"] = result
                    run["status"] = final_status
                    run["updated_at"] = utc_now()
                    self._append_event(run, "Execution bridge finished", {
                        "final_status": final_status,
                        "result_keys": sorted(list(result.keys()))
                    })
                    self._write_store(store)
                    return deepcopy(run)

            # multi-step loop
            loop_history: List[Dict[str, Any]] = []
            current_status = "executing"
            last_result: Dict[str, Any] = {}

            for idx in range(max_steps):
                with self._lock:
                    store = self._read_store()
                    run = self._find_run_ref(store, decision_id)
                    if not run:
                        return {"found": False, "decision_id": decision_id, "message": "Decision run disappeared"}
                    self._ensure_loop(run)
                    run["loop"]["step_count"] = idx + 1
                    self._append_event(run, "Loop step started", {"step": idx + 1, "action": run["decision"]["action"]})
                    self._write_store(store)

                step = self._bridge_once(run, base_url)
                step_status = step["status"]
                step_result = step["result"]

                loop_item = {
                    "step": idx + 1,
                    "action": run["decision"]["action"],
                    "target_layer": run["decision"]["target_layer"],
                    "status": step_status,
                    "result_keys": sorted(list(step_result.keys())),
                    "ts": utc_now(),
                }
                loop_history.append(loop_item)
                last_result = step_result

                with self._lock:
                    store = self._read_store()
                    run = self._find_run_ref(store, decision_id)
                    if not run:
                        return {"found": False, "decision_id": decision_id, "message": "Decision run disappeared"}
                    self._ensure_loop(run)
                    run["loop"]["history"] = loop_history

                    if step_status == "blocked":
                        current_status = "blocked"
                        run["loop"]["final_outcome"] = "approval_required"
                        self._append_event(run, "Loop stopped due to approval requirement", {"step": idx + 1})
                        break

                    if step_status == "failed":
                        current_status = "failed"
                        run["loop"]["final_outcome"] = "execution_failed"
                        self._append_event(run, "Loop stopped due to execution failure", {"step": idx + 1})
                        break

                    # if memory guidance was retrieved and tools are still needed, pivot once into tools
                    if run["decision"]["action"] == "retrieve_memory_guidance" and run.get("requires_tools"):
                        run["decision"] = {
                            "action": "plan_tool_execution",
                            "reason": "Memory guidance retrieved; next step is tool execution",
                            "target_layer": "tools",
                            "confidence": 0.91,
                        }
                        self._append_event(run, "Decision pivoted after memory retrieval", {
                            "next_action": "plan_tool_execution"
                        })
                        self._write_store(store)
                        continue

                    current_status = "completed"
                    run["loop"]["final_outcome"] = "goal_progressed"
                    self._append_event(run, "Loop completed", {"step": idx + 1})
                    self._write_store(store)
                    break

            with self._lock:
                store = self._read_store()
                run = self._find_run_ref(store, decision_id)
                if not run:
                    return {"found": False, "decision_id": decision_id, "message": "Decision run disappeared"}

                self._ensure_loop(run)
                run["execution_result"] = last_result
                run["status"] = current_status
                run["updated_at"] = utc_now()
                self._append_event(run, "Execution bridge finished", {
                    "final_status": current_status,
                    "loop_steps": run["loop"]["step_count"],
                    "result_keys": sorted(list(last_result.keys())) if isinstance(last_result, dict) else [],
                })
                self._write_store(store)
                return deepcopy(run)

        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                body = ""
            result = {"error": f"HTTPError {exc.code}", "body": body}
            final_status = "failed"
        except Exception as exc:
            result = {"error": str(exc)}
            final_status = "failed"

        with self._lock:
            store = self._read_store()
            run = self._find_run_ref(store, decision_id)
            if not run:
                return {
                    "found": False,
                    "decision_id": decision_id,
                    "message": "Decision run disappeared during execution",
                }
            self._ensure_loop(run)
            run["execution_result"] = result
            run["status"] = final_status
            run["updated_at"] = utc_now()
            self._append_event(run, "Execution bridge finished", {
                "final_status": final_status,
                "result_keys": sorted(list(result.keys()))
            })
            self._write_store(store)
            return deepcopy(run)


service = AutonomousDecisionEngine(Path(__file__).resolve().parents[2] / "artifacts" / "autonomous_decision")
