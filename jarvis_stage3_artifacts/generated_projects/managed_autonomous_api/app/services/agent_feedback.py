from __future__ import annotations

import json
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentFeedbackService:
    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.store_path = self.base_dir / "agent_scores.json"
        self._lock = threading.RLock()

    def _default_store(self) -> Dict[str, Any]:
        return {
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "agents": {}
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
            if "agents" not in data or not isinstance(data["agents"], dict):
                data["agents"] = {}
            if "created_at" not in data:
                data["created_at"] = utc_now()
            if "updated_at" not in data:
                data["updated_at"] = utc_now()
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

    def health(self) -> Dict[str, Any]:
        with self._lock:
            store = self._read_store()
            return {
                "status": "healthy",
                "store_path": str(self.store_path),
                "agents_count": len(store["agents"]),
                "updated_at": store["updated_at"],
            }

    def _get_agent(self, store: Dict[str, Any], agent_id: str, role: str | None = None) -> Dict[str, Any]:
        agents = store["agents"]
        if agent_id not in agents:
            agents[agent_id] = {
                "agent_id": agent_id,
                "role": role or "unknown",
                "created_at": utc_now(),
                "updated_at": utc_now(),
                "score": 50.0,
                "decision": "observe",
                "totals": {
                    "runs": 0,
                    "successes": 0,
                    "failures": 0,
                    "qa_passes": 0,
                    "qa_fails": 0,
                    "retries": 0,
                },
                "kpis": {
                    "stage_success_rate": 0.0,
                    "qa_pass_rate": 0.0,
                    "retry_rate": 0.0,
                    "stability_score": 50.0,
                },
                "history": [],
            }
        elif role and agents[agent_id].get("role") in (None, "", "unknown"):
            agents[agent_id]["role"] = role
        return agents[agent_id]

    def evaluate(
        self,
        agent_id: str,
        role: str,
        stage_success: bool,
        qa_pass: bool,
        retry_count: int,
    ) -> Dict[str, Any]:
        with self._lock:
            store = self._read_store()
            agent = self._get_agent(store, agent_id, role)

            totals = agent["totals"]
            totals["runs"] += 1
            if stage_success:
                totals["successes"] += 1
            else:
                totals["failures"] += 1

            if qa_pass:
                totals["qa_passes"] += 1
            else:
                totals["qa_fails"] += 1

            totals["retries"] += max(0, int(retry_count))

            runs = max(1, totals["runs"])
            stage_success_rate = totals["successes"] / runs
            qa_pass_rate = totals["qa_passes"] / runs
            retry_rate = totals["retries"] / runs

            stability_score = (
                stage_success_rate * 50.0 +
                qa_pass_rate * 35.0 +
                max(0.0, 1.0 - min(retry_rate / 3.0, 1.0)) * 15.0
            )

            if stability_score >= 85:
                decision = "upgrade_candidate"
            elif stability_score >= 65:
                decision = "healthy"
            elif stability_score >= 45:
                decision = "observe"
            elif stability_score >= 25:
                decision = "downgrade_candidate"
            else:
                decision = "quarantine_candidate"

            agent["kpis"] = {
                "stage_success_rate": round(stage_success_rate, 4),
                "qa_pass_rate": round(qa_pass_rate, 4),
                "retry_rate": round(retry_rate, 4),
                "stability_score": round(stability_score, 2),
            }
            agent["score"] = round(stability_score, 2)
            agent["decision"] = decision
            agent["updated_at"] = utc_now()

            history_item = {
                "ts": utc_now(),
                "stage_success": bool(stage_success),
                "qa_pass": bool(qa_pass),
                "retry_count": int(retry_count),
                "score_after": agent["score"],
                "decision_after": decision,
            }
            agent["history"].append(history_item)
            if len(agent["history"]) > 100:
                agent["history"] = agent["history"][-100:]

            self._write_store(store)
            return deepcopy(agent)

    def get_agent(self, agent_id: str) -> Dict[str, Any]:
        with self._lock:
            store = self._read_store()
            agent = store["agents"].get(agent_id)
            if not agent:
                return {
                    "found": False,
                    "agent_id": agent_id,
                    "message": "Agent score record not found",
                }
            return deepcopy(agent)

    def list_agents(self) -> Dict[str, Any]:
        with self._lock:
            store = self._read_store()
            agents: List[Dict[str, Any]] = list(store["agents"].values())
            agents.sort(key=lambda x: (-float(x.get("score", 0.0)), x.get("agent_id", "")))
            return {
                "count": len(agents),
                "items": deepcopy(agents),
            }


service = AgentFeedbackService(Path(__file__).resolve().parents[2] / "artifacts" / "agent_feedback")
