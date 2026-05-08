from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.time_utils import utc_now
from app.agents.registry import AgentRegistry
from app.services.reliability import ReliabilityManager


BASE_DIR = Path(__file__).resolve().parents[2]
ARTIFACTS_DIR = BASE_DIR / "artifacts"
AUTONOMY_FILE = ARTIFACTS_DIR / "autonomy_state.json"

MAX_AUTONOMOUS_ACTIONS_PER_AGENT = 20
DEFAULT_MIN_AGENT_SCORE = 0.45


def _template() -> Dict[str, Any]:
    return {
        "version": 1,
        "updated_at": None,
        "agent_scores": {},
        "approval_queue": [],
        "policy_decisions": []
    }


def _read_json(path: Path, fallback: Dict[str, Any]) -> Dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(json.dumps(fallback, indent=2, ensure_ascii=False), encoding="utf-8")
        return fallback
    try:
        raw = path.read_text(encoding="utf-8-sig").strip()
        if not raw:
            path.write_text(json.dumps(fallback, indent=2, ensure_ascii=False), encoding="utf-8")
            return fallback
        data = json.loads(raw)
        return data if isinstance(data, dict) else fallback
    except Exception:
        return fallback


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


class AutonomyControlService:
    def __init__(self) -> None:
        self.path = AUTONOMY_FILE
        self.registry = AgentRegistry()
        self.reliability = ReliabilityManager()

    def load(self) -> Dict[str, Any]:
        return _read_json(self.path, _template())

    def save(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        doc["updated_at"] = utc_now().isoformat()
        _write_json(self.path, doc)
        return doc

    def score_agents(self) -> Dict[str, Any]:
        doc = self.load()
        runs = self.reliability.list_runs()
        agents = self.registry.list_agents()

        scores: Dict[str, Dict[str, Any]] = {}

        for agent in agents:
            scores[agent.agent_id] = {
                "agent_id": agent.agent_id,
                "role": agent.role,
                "score": 0.5,
                "runs_seen": 0,
                "quarantined_involved": 0,
                "retry_pressure": 0
            }

        for run in runs:
            retry_count = run.get("retry_count", 0)
            is_quarantined = run.get("status") == "quarantined"

            for stage in run.get("stages", []):
                agent_id = stage.get("agent_id")
                if agent_id not in scores:
                    continue

                scores[agent_id]["runs_seen"] += 1
                scores[agent_id]["retry_pressure"] += retry_count

                if is_quarantined:
                    scores[agent_id]["quarantined_involved"] += 1

        for agent_id, item in scores.items():
            penalty = 0.0
            penalty += item["quarantined_involved"] * 0.08
            penalty += item["retry_pressure"] * 0.03

            base = 0.9 if item["runs_seen"] > 0 else 0.5
            score = max(0.0, min(1.0, round(base - penalty, 4)))
            item["score"] = score

        doc["agent_scores"] = scores
        self.save(doc)

        return {
            "status": "ok",
            "agent_scores": scores
        }

    def get_scores(self) -> Dict[str, Any]:
        doc = self.load()
        return {
            "status": "ok",
            "agent_scores": doc.get("agent_scores", {})
        }

    def evaluate_action(
        self,
        agent_id: str,
        action_type: str,
        requested_tools: List[str],
        objective: str
    ) -> Dict[str, Any]:
        doc = self.load()
        agent = self.registry.get(agent_id)
        if not agent:
            return {"status": "error", "error": f"unknown agent: {agent_id}"}

        if agent.status != "active":
            return {"status": "blocked", "reason": f"agent not active: {agent.status}"}

        scores = doc.get("agent_scores", {})
        agent_score = scores.get(agent_id, {}).get("score", 0.5)

        risky_tools = {"shell", "http", "filesystem", "python"}
        requested_risky = sorted(list(set(requested_tools).intersection(risky_tools)))

        requires_approval = False
        reasons = []

        if agent_score < DEFAULT_MIN_AGENT_SCORE:
            requires_approval = True
            reasons.append(f"agent score below threshold: {agent_score}")

        if len(requested_risky) >= 2:
            requires_approval = True
            reasons.append(f"multiple risky tools requested: {requested_risky}")

        if action_type in {"delete", "destructive", "high_risk"}:
            requires_approval = True
            reasons.append(f"high risk action_type: {action_type}")

        decision = {
            "decision_id": f"dec_{len(doc['policy_decisions']) + 1:05d}",
            "agent_id": agent_id,
            "action_type": action_type,
            "requested_tools": requested_tools,
            "objective": objective,
            "requires_approval": requires_approval,
            "reasons": reasons,
            "created_at": utc_now().isoformat()
        }

        doc["policy_decisions"].append(decision)

        if requires_approval:
            approval_item = {
                "approval_id": f"approval_{len(doc['approval_queue']) + 1:05d}",
                "agent_id": agent_id,
                "action_type": action_type,
                "objective": objective,
                "requested_tools": requested_tools,
                "reasons": reasons,
                "status": "pending",
                "created_at": utc_now().isoformat()
            }
            doc["approval_queue"].append(approval_item)

        self.save(doc)

        return {
            "status": "ok",
            "decision": decision
        }

    def list_approvals(self) -> Dict[str, Any]:
        doc = self.load()
        return {
            "status": "ok",
            "count": len(doc.get("approval_queue", [])),
            "approval_queue": doc.get("approval_queue", [])
        }

    def decide_approval(self, approval_id: str, decision: str) -> Dict[str, Any]:
        if decision not in {"approved", "rejected"}:
            return {"status": "error", "error": "decision must be approved or rejected"}

        doc = self.load()

        for item in doc.get("approval_queue", []):
            if item["approval_id"] == approval_id:
                item["status"] = decision
                item["updated_at"] = utc_now().isoformat()
                self.save(doc)
                return {"status": "ok", "approval": item}

        return {"status": "error", "error": f"approval_id not found: {approval_id}"}

    def auto_disable_low_score_agents(self, threshold: float = 0.25) -> Dict[str, Any]:
        score_report = self.score_agents()
        scores = score_report["agent_scores"]

        disabled = []
        for agent_id, item in scores.items():
            if item["score"] < threshold:
                try:
                    agent = self.registry.set_status(agent_id, "disabled")
                    disabled.append({
                        "agent_id": agent_id,
                        "new_status": agent.status,
                        "score": item["score"]
                    })
                except Exception:
                    pass

        return {
            "status": "ok",
            "disabled_count": len(disabled),
            "disabled_agents": disabled
        }
