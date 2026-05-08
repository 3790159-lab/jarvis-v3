from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from app.services.jarvis_advanced_mutation_lane import JarvisAdvancedMutationLane
from app.services.jarvis_safe_mutation_foundation import SafeMutationFoundation


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class DependencyAwarePatchPlan:
    plan_id: str
    goal: str
    task: str
    patch_class: str
    risk_lane: str
    recommended_action: str
    main_file: str
    verify_file: str
    smoke_file: str
    target_files: List[str]
    support_files: List[str]
    operations: List[Dict[str, Any]]
    validation_plan: List[str]
    reasons: List[str] = field(default_factory=list)
    next_best_action: str = ""
    created_at: str = field(default_factory=utc_now_iso)


@dataclass
class DependencyPatchExecutionResult:
    plan_id: str
    status: str
    recommended_action: str
    mutation_id: Optional[str]
    final_outcome: str
    apply_status: str
    validation_status: str
    rollback_status: str
    touched_files: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    next_best_action: str = ""
    created_at: str = field(default_factory=utc_now_iso)


class JarvisDependencyAwarePatchPlanner:
    """
    Block 7.3 + 7.4:
    - creates dependency-aware patch bundles:
      main code file + verify file + smoke file
    - routes plan through Advanced Mutation Lane
    - applies only when advanced lane allows apply
    - executes through Safe Mutation Foundation
    """

    def __init__(
        self,
        project_root: str | Path,
        artifacts_root: Optional[str | Path] = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.artifacts_root = (
            Path(artifacts_root).resolve()
            if artifacts_root
            else self.project_root / "jarvis_stage3_artifacts" / "dependency_aware_patch_planner"
        )

        self.plans_dir = ensure_dir(self.artifacts_root / "plans")
        self.executions_dir = ensure_dir(self.artifacts_root / "executions")
        self.logs_dir = ensure_dir(self.artifacts_root / "logs")
        self.runtime_dir = ensure_dir(self.artifacts_root / "runtime")

        self.advanced_lane = JarvisAdvancedMutationLane(project_root=self.project_root)
        self.foundation = SafeMutationFoundation(project_root=self.project_root)

    def _write_json(self, path: Path, payload: Any) -> None:
        ensure_dir(path.parent)
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def _log(self, level: str, message: str) -> None:
        line = f"[{utc_now_iso()}] [{level.upper()}] {message}"
        print(line)
        with (self.logs_dir / "dependency_aware_patch_planner.log").open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _safe_slug(self, text: str) -> str:
        cleaned = []
        for ch in text.lower():
            if ch.isalnum():
                cleaned.append(ch)
            elif ch in {" ", "-", "_"}:
                cleaned.append("_")
        slug = "".join(cleaned).strip("_")
        while "__" in slug:
            slug = slug.replace("__", "_")
        return slug[:64] or "patch"

    def _default_main_content(self, plan_id: str, goal: str, task: str) -> str:
        return (
            '"""Generated dependency-aware patch module.\n'
            "\n"
            "This file is intentionally small and safe. It is created by\n"
            "JarvisDependencyAwarePatchPlanner as a bounded patch artifact.\n"
            '"""\n'
            "\n"
            "from __future__ import annotations\n"
            "\n"
            "from dataclasses import dataclass\n"
            "from datetime import datetime, timezone\n"
            "\n"
            "\n"
            "def utc_now_iso() -> str:\n"
            "    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()\n"
            "\n"
            "\n"
            "@dataclass\n"
            "class PatchProbeResult:\n"
            "    plan_id: str\n"
            "    goal: str\n"
            "    task: str\n"
            "    status: str\n"
            "    created_at: str\n"
            "\n"
            "\n"
            "def run_patch_probe() -> PatchProbeResult:\n"
            "    return PatchProbeResult(\n"
            f'        plan_id="{plan_id}",\n'
            f'        goal={goal!r},\n'
            f'        task={task!r},\n'
            '        status="ok",\n'
            "        created_at=utc_now_iso(),\n"
            "    )\n"
        )

    def _default_verify_content(self, main_module_import: str) -> str:
        return (
            "from __future__ import annotations\n"
            "\n"
            "import json\n"
            "import sys\n"
            "from pathlib import Path\n"
            "\n"
            "PROJECT_ROOT = Path(__file__).resolve().parents[2]\n"
            "if str(PROJECT_ROOT) not in sys.path:\n"
            "    sys.path.insert(0, str(PROJECT_ROOT))\n"
            "\n"
            f"from {main_module_import} import run_patch_probe\n"
            "\n"
            "\n"
            "def main() -> int:\n"
            "    result = run_patch_probe()\n"
            "    payload = {\n"
            "        'plan_id': result.plan_id,\n"
            "        'goal': result.goal,\n"
            "        'task': result.task,\n"
            "        'status': result.status,\n"
            "        'created_at': result.created_at,\n"
            "    }\n"
            "    out = PROJECT_ROOT / 'jarvis_stage3_artifacts' / 'temp' / f'{result.plan_id}_verify_result.json'\n"
            "    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')\n"
            "    print(json.dumps(payload, ensure_ascii=False, indent=2))\n"
            "    return 0 if result.status == 'ok' else 1\n"
            "\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    raise SystemExit(main())\n"
        )

    def _default_smoke_content(self, verify_file: str) -> str:
        verify_path = ".\\" + verify_file.replace("/", "\\")
        return (
            "param(\n"
            '    [string]$ProjectRoot = "C:\\Users\\Daniil Lapin\\Downloads\\supervisor_v1_5_smart_telegram (1)\\supervisor_v1_5_smart_telegram"\n'
            ")\n"
            "\n"
            "Set-ExecutionPolicy -Scope Process Bypass -Force\n"
            "Set-StrictMode -Version Latest\n"
            '$ErrorActionPreference = "Stop"\n'
            "\n"
            "Set-Location $ProjectRoot\n"
            "$PyExe = Join-Path $ProjectRoot '.venv\\Scripts\\python.exe'\n"
            "if (-not (Test-Path -LiteralPath $PyExe)) { $PyExe = 'python' }\n"
            "$env:PYTHONPATH = $ProjectRoot\n"
            "\n"
            'Write-Host "=== RUN DEPENDENCY-AWARE VERIFY ===" -ForegroundColor Cyan\n'
            f'& $PyExe "{verify_path}"\n'
            'if ($LASTEXITCODE -ne 0) { throw "dependency-aware verify failed" }\n'
            "\n"
            'Write-Host "DEPENDENCY-AWARE PATCH SMOKE OK" -ForegroundColor Green\n'
        )

    def build_plan(
        self,
        goal: str,
        task: str,
        patch_name: Optional[str] = None,
        estimated_lines_changed: int = 140,
    ) -> DependencyAwarePatchPlan:
        slug = self._safe_slug(patch_name or goal)
        plan_id = "depplan_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]

        main_file = f"jarvis_stage3_artifacts/generated_modules/{slug}_{plan_id}.py"
        verify_file = f"jarvis_stage3_artifacts/temp/{slug}_{plan_id}_verify.py"
        smoke_file = f"scripts/{slug}_{plan_id}_smoke.ps1"

        module_import = main_file[:-3].replace("/", ".").replace("\\", ".")

        target_files = [main_file]
        support_files = [verify_file, smoke_file]

        decision = self.advanced_lane.decide(
            goal=goal,
            task=task,
            target_files=target_files,
            support_files=support_files,
            estimated_lines_changed=estimated_lines_changed,
        )

        operations = [
            {
                "op": "create_file",
                "path": main_file,
                "content": self._default_main_content(plan_id=plan_id, goal=goal, task=task),
                "create_if_missing": True,
            },
            {
                "op": "create_file",
                "path": verify_file,
                "content": self._default_verify_content(main_module_import=module_import),
                "create_if_missing": True,
            },
            {
                "op": "create_file",
                "path": smoke_file,
                "content": self._default_smoke_content(verify_file=verify_file),
                "create_if_missing": True,
            },
        ]

        validation_plan = ["py_compile_main", "py_compile_verify", "run_verify", "health_check"]

        plan = DependencyAwarePatchPlan(
            plan_id=plan_id,
            goal=goal,
            task=task,
            patch_class=decision.patch_class,
            risk_lane=decision.risk_lane,
            recommended_action=decision.recommended_action,
            main_file=main_file,
            verify_file=verify_file,
            smoke_file=smoke_file,
            target_files=target_files,
            support_files=support_files,
            operations=operations,
            validation_plan=validation_plan,
            reasons=list(decision.reasons),
            next_best_action=decision.next_best_action,
        )
        self._write_json(self.plans_dir / f"{plan_id}.json", asdict(plan))
        self._log(
            "info",
            f"build_plan plan_id={plan_id} action={plan.recommended_action} class={plan.patch_class} risk={plan.risk_lane}",
        )
        return plan

    def execute_plan(self, plan: DependencyAwarePatchPlan) -> DependencyPatchExecutionResult:
        if plan.recommended_action != "apply":
            result = DependencyPatchExecutionResult(
                plan_id=plan.plan_id,
                status="not_applied",
                recommended_action=plan.recommended_action,
                mutation_id=None,
                final_outcome="artifact_only",
                apply_status="not_started",
                validation_status="not_started",
                rollback_status="not_needed",
                touched_files=[],
                reasons=list(plan.reasons),
                next_best_action=plan.next_best_action,
            )
            self._write_json(self.executions_dir / f"{plan.plan_id}.json", asdict(result))
            return result

        candidate = self.foundation.create_candidate(
            goal=plan.goal,
            reason=plan.task,
            change_type=plan.patch_class,
            target_files=plan.target_files + plan.support_files,
            expected_effect="Create dependency-aware patch bundle with code, verify, and smoke support",
            risk_level="low",
            reversible=True,
            estimated_validation_scope="local_compile_and_health",
            operations=plan.operations,
            metadata={
                "block": "dependency_aware_patch_planner",
                "plan_id": plan.plan_id,
                "patch_class": plan.patch_class,
                "risk_lane": plan.risk_lane,
                "advanced_lane_risk": plan.risk_lane,
                "foundation_risk_level": "low",
            },
        )

        mutation_result = self.foundation.execute_candidate(candidate)

        result = DependencyPatchExecutionResult(
            plan_id=plan.plan_id,
            status="completed" if mutation_result.final_outcome == "accepted" else "rejected",
            recommended_action=plan.recommended_action,
            mutation_id=mutation_result.mutation_id,
            final_outcome=mutation_result.final_outcome,
            apply_status=mutation_result.apply_status,
            validation_status=mutation_result.validation_status,
            rollback_status=mutation_result.rollback_status,
            touched_files=mutation_result.touched_files,
            reasons=[mutation_result.reason],
            next_best_action=(
                "Run generated smoke script and integrate successful patch archetype into advanced lane."
                if mutation_result.final_outcome == "accepted"
                else "Inspect validation failure and reduce patch scope."
            ),
        )
        self._write_json(self.executions_dir / f"{plan.plan_id}.json", asdict(result))
        self._log(
            "info",
            f"execute_plan plan_id={plan.plan_id} outcome={result.final_outcome} status={result.status}",
        )
        return result

    def build_and_execute(
        self,
        goal: str,
        task: str,
        patch_name: Optional[str] = None,
        estimated_lines_changed: int = 120,
    ) -> DependencyPatchExecutionResult:
        plan = self.build_plan(
            goal=goal,
            task=task,
            patch_name=patch_name,
            estimated_lines_changed=estimated_lines_changed,
        )
        return self.execute_plan(plan)

    def collect_metrics(self) -> Dict[str, Any]:
        plan_files = list(self.plans_dir.glob("*.json"))
        execution_files = list(self.executions_dir.glob("*.json"))

        completed = 0
        not_applied = 0
        rejected = 0
        accepted_mutations = 0

        for path in execution_files:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            status = data.get("status")
            if status == "completed":
                completed += 1
            elif status == "not_applied":
                not_applied += 1
            elif status == "rejected":
                rejected += 1
            if data.get("final_outcome") == "accepted":
                accepted_mutations += 1

        metrics = {
            "plans_count": len(plan_files),
            "executions_count": len(execution_files),
            "completed_count": completed,
            "not_applied_count": not_applied,
            "rejected_count": rejected,
            "accepted_mutations": accepted_mutations,
            "collected_at": utc_now_iso(),
            "artifacts_root": str(self.artifacts_root),
        }
        self._write_json(self.runtime_dir / "metrics_summary.json", metrics)
        return metrics