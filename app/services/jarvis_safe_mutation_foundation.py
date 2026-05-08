from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
import hashlib
import py_compile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.error import URLError, HTTPError
from urllib.request import urlopen


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class MutationOperation:
    op: str
    path: str
    content: Optional[str] = None
    pattern: Optional[str] = None
    replacement: Optional[str] = None
    anchor: Optional[str] = None
    insert_text: Optional[str] = None
    create_if_missing: bool = False
    allow_multiple: bool = False


@dataclass
class MutationCandidate:
    mutation_id: str
    goal: str
    reason: str
    change_type: str
    target_files: List[str]
    expected_effect: str
    risk_level: str
    reversible: bool
    estimated_validation_scope: str
    operations: List[MutationOperation] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MutationSnapshotFile:
    path: str
    existed_before: bool
    sha256_before: Optional[str]
    backup_path: Optional[str]
    size_before: Optional[int]


@dataclass
class MutationSnapshot:
    snapshot_id: str
    mutation_id: str
    created_at: str
    files: List[MutationSnapshotFile]


@dataclass
class ValidationCheck:
    name: str
    ok: bool
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MutationResult:
    mutation_id: str
    status: str
    apply_status: str
    validation_status: str
    rollback_status: str
    touched_files: List[str]
    checks: List[ValidationCheck]
    final_outcome: str
    reason: str
    started_at: str
    finished_at: str
    duration_seconds: float


class SafeMutationError(RuntimeError):
    pass


class SafeMutationFoundation:
    """
    Block 1 foundation:
    - explicit mutation records
    - snapshot before apply
    - constrained apply operations
    - validation chain
    - rollback on failure
    - accepted/rejected summaries
    """

    ALLOWED_EXTENSIONS = {".py", ".ps1", ".json", ".md", ".txt", ".yaml", ".yml"}

    def __init__(
        self,
        project_root: str | Path,
        artifacts_root: Optional[str | Path] = None,
        allowed_roots: Optional[Sequence[str]] = None,
        base_url: str = "http://127.0.0.1:8015",
        python_exe: Optional[str] = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.artifacts_root = (
            Path(artifacts_root).resolve()
            if artifacts_root
            else self.project_root / "jarvis_stage3_artifacts" / "mutation_runtime"
        )
        self.base_url = base_url.rstrip("/")
        self.python_exe = python_exe or sys.executable

        default_allowed_roots = [
            "app",
            "scripts",
            "jarvis_stage3_artifacts/generated_modules",
            "jarvis_stage3_artifacts/temp",
        ]
        self.allowed_roots = [self.project_root / p for p in (allowed_roots or default_allowed_roots)]

        self.candidates_dir = ensure_dir(self.artifacts_root / "candidates")
        self.plans_dir = ensure_dir(self.artifacts_root / "plans")
        self.snapshots_dir = ensure_dir(self.artifacts_root / "snapshots")
        self.validation_dir = ensure_dir(self.artifacts_root / "validation")
        self.rollback_dir = ensure_dir(self.artifacts_root / "rollback")
        self.accepted_dir = ensure_dir(self.artifacts_root / "accepted")
        self.rejected_dir = ensure_dir(self.artifacts_root / "rejected")
        self.logs_dir = ensure_dir(self.artifacts_root / "logs")
        self.runtime_dir = ensure_dir(self.artifacts_root / "runtime")

    # ------------------------------------------------------------------
    # basic utilities
    # ------------------------------------------------------------------
    def _log(self, level: str, message: str) -> None:
        line = f"[{utc_now_iso()}] [{level.upper()}] {message}"
        print(line)
        log_path = self.logs_dir / "mutation_runtime.log"
        with log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _write_json(self, path: Path, payload: Any) -> None:
        ensure_dir(path.parent)
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def _normalize_rel_path(self, rel_path: str) -> Path:
        p = (self.project_root / rel_path).resolve()
        if not str(p).startswith(str(self.project_root)):
            raise SafeMutationError(f"Path escapes project root: {rel_path}")
        return p

    def _path_is_allowed(self, path: Path) -> bool:
        if path.suffix.lower() not in self.ALLOWED_EXTENSIONS:
            return False
        return any(str(path).startswith(str(root.resolve())) for root in self.allowed_roots)

    def _file_info(self, path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {
                "path": str(path),
                "exists": False,
                "sha256": None,
                "size": None,
                "modified_at": None,
            }
        return {
            "path": str(path),
            "exists": True,
            "sha256": sha256_file(path),
            "size": path.stat().st_size,
            "modified_at": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
        }

    # ------------------------------------------------------------------
    # candidate / planning
    # ------------------------------------------------------------------
    def create_candidate(
        self,
        goal: str,
        reason: str,
        change_type: str,
        target_files: Sequence[str],
        expected_effect: str,
        risk_level: str = "low",
        reversible: bool = True,
        estimated_validation_scope: str = "local_compile_and_health",
        operations: Optional[Sequence[Dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> MutationCandidate:
        mutation_id = "mut_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
        ops = [MutationOperation(**op) for op in (operations or [])]
        candidate = MutationCandidate(
            mutation_id=mutation_id,
            goal=goal,
            reason=reason,
            change_type=change_type,
            target_files=list(target_files),
            expected_effect=expected_effect,
            risk_level=risk_level,
            reversible=reversible,
            estimated_validation_scope=estimated_validation_scope,
            operations=ops,
            metadata=metadata or {},
        )
        self._write_json(self.candidates_dir / f"{mutation_id}.json", self._candidate_payload(candidate))
        return candidate

    def _candidate_payload(self, candidate: MutationCandidate) -> Dict[str, Any]:
        payload = asdict(candidate)
        payload["created_at"] = utc_now_iso()
        payload["operations"] = [asdict(op) for op in candidate.operations]
        return payload

    def assess_candidate(self, candidate: MutationCandidate) -> Dict[str, Any]:
        reasons: List[str] = []
        allowed = True

        if candidate.risk_level.lower() != "low":
            reasons.append(f"risk_level_not_allowed:{candidate.risk_level}")
            allowed = False

        if not candidate.operations:
            reasons.append("no_operations_defined")
            allowed = False

        touched: List[str] = []
        for op in candidate.operations:
            path = self._normalize_rel_path(op.path)
            touched.append(str(path))
            if not self._path_is_allowed(path):
                allowed = False
                reasons.append(f"path_not_allowed:{op.path}")

            if op.op not in {"replace_text", "insert_after", "insert_before", "append_block", "create_file"}:
                allowed = False
                reasons.append(f"unsupported_op:{op.op}")

        if len(set(touched)) > 3:
            allowed = False
            reasons.append("too_many_touched_files")

        score = 10
        if candidate.risk_level == "low":
            score += 25
        if len(set(touched)) == 1:
            score += 20
        if candidate.change_type in {"small_code_patch", "guard_patch", "logging_patch", "helper_patch"}:
            score += 20
        if candidate.estimated_validation_scope == "local_compile_and_health":
            score += 15
        if candidate.reversible:
            score += 10
        score -= min(40, len(reasons) * 10)

        decision = {
            "mutation_id": candidate.mutation_id,
            "allowed": allowed,
            "reasons": reasons,
            "score": max(0, min(100, score)),
            "assessed_at": utc_now_iso(),
            "touched_files_planned": sorted(set(touched)),
        }
        self._write_json(self.plans_dir / f"{candidate.mutation_id}.json", decision)
        return decision

    # ------------------------------------------------------------------
    # snapshot / rollback
    # ------------------------------------------------------------------
    def create_snapshot(self, candidate: MutationCandidate) -> MutationSnapshot:
        snapshot_id = "snap_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
        snapshot_root = ensure_dir(self.snapshots_dir / snapshot_id)
        files: List[MutationSnapshotFile] = []

        unique_paths = sorted(set(candidate.target_files + [op.path for op in candidate.operations]))
        for rel_path in unique_paths:
            path = self._normalize_rel_path(rel_path)
            existed_before = path.exists()
            sha_before = sha256_file(path) if existed_before else None
            size_before = path.stat().st_size if existed_before else None
            backup_path = None

            if existed_before:
                backup_rel = Path("backup") / rel_path
                backup_abs = snapshot_root / backup_rel
                ensure_dir(backup_abs.parent)
                shutil.copy2(path, backup_abs)
                backup_path = str(backup_abs)

            files.append(
                MutationSnapshotFile(
                    path=str(path),
                    existed_before=existed_before,
                    sha256_before=sha_before,
                    backup_path=backup_path,
                    size_before=size_before,
                )
            )

        snapshot = MutationSnapshot(
            snapshot_id=snapshot_id,
            mutation_id=candidate.mutation_id,
            created_at=utc_now_iso(),
            files=files,
        )
        self._write_json(snapshot_root / "snapshot.json", asdict(snapshot))
        return snapshot

    def rollback_snapshot(self, snapshot: MutationSnapshot, reason: str) -> Dict[str, Any]:
        restored: List[str] = []
        removed_new: List[str] = []
        failures: List[Dict[str, Any]] = []

        for f in snapshot.files:
            path = Path(f.path)
            try:
                if f.existed_before:
                    if not f.backup_path:
                        raise SafeMutationError(f"Missing backup path for {f.path}")
                    backup_path = Path(f.backup_path)
                    ensure_dir(path.parent)
                    shutil.copy2(backup_path, path)
                    restored.append(str(path))
                else:
                    if path.exists():
                        path.unlink()
                        removed_new.append(str(path))
            except Exception as exc:  # noqa: BLE001
                failures.append({"path": str(path), "error": str(exc)})

        payload = {
            "snapshot_id": snapshot.snapshot_id,
            "mutation_id": snapshot.mutation_id,
            "reason": reason,
            "restored_files": restored,
            "removed_new_files": removed_new,
            "failures": failures,
            "rolled_back_at": utc_now_iso(),
            "ok": len(failures) == 0,
        }
        self._write_json(self.rollback_dir / f"{snapshot.mutation_id}.json", payload)
        return payload

    # ------------------------------------------------------------------
    # apply operations
    # ------------------------------------------------------------------
    def _read_text(self, path: Path) -> str:
        if not path.exists():
            raise SafeMutationError(f"Target file does not exist: {path}")
        return path.read_text(encoding="utf-8")

    def _write_text(self, path: Path, text: str) -> None:
        ensure_dir(path.parent)
        path.write_text(text, encoding="utf-8", newline="\n")

    def _apply_operation(self, op: MutationOperation) -> str:
        path = self._normalize_rel_path(op.path)
        if not self._path_is_allowed(path):
            raise SafeMutationError(f"Path not allowed: {op.path}")

        if op.op == "create_file":
            if path.exists() and not op.create_if_missing:
                raise SafeMutationError(f"File already exists: {op.path}")
            self._write_text(path, op.content or "")
            return str(path)

        original = self._read_text(path)
        updated = original

        if op.op == "replace_text":
            if not op.pattern:
                raise SafeMutationError(f"replace_text requires pattern: {op.path}")
            if op.replacement is None:
                raise SafeMutationError(f"replace_text requires replacement: {op.path}")
            count = 0 if op.allow_multiple else 1
            updated, n = re.subn(op.pattern, op.replacement, original, count=count, flags=re.MULTILINE | re.DOTALL)
            if n == 0:
                raise SafeMutationError(f"Pattern not found in {op.path}: {op.pattern}")

        elif op.op == "insert_after":
            if op.anchor is None or op.insert_text is None:
                raise SafeMutationError(f"insert_after requires anchor and insert_text: {op.path}")
            if op.anchor not in original:
                raise SafeMutationError(f"Anchor not found in {op.path}: {op.anchor}")
            updated = original.replace(op.anchor, op.anchor + op.insert_text, 1)

        elif op.op == "insert_before":
            if op.anchor is None or op.insert_text is None:
                raise SafeMutationError(f"insert_before requires anchor and insert_text: {op.path}")
            if op.anchor not in original:
                raise SafeMutationError(f"Anchor not found in {op.path}: {op.anchor}")
            updated = original.replace(op.anchor, op.insert_text + op.anchor, 1)

        elif op.op == "append_block":
            if op.insert_text is None:
                raise SafeMutationError(f"append_block requires insert_text: {op.path}")
            if original and not original.endswith("\n"):
                updated = original + "\n" + op.insert_text
            else:
                updated = original + op.insert_text

        else:
            raise SafeMutationError(f"Unsupported operation: {op.op}")

        if updated == original:
            raise SafeMutationError(f"No-op mutation prevented for {op.path}")

        self._write_text(path, updated)
        return str(path)

    def apply_candidate(self, candidate: MutationCandidate) -> Dict[str, Any]:
        touched: List[str] = []
        op_results: List[Dict[str, Any]] = []

        for op in candidate.operations:
            started = time.time()
            path = self._apply_operation(op)
            touched.append(path)
            op_results.append(
                {
                    "op": op.op,
                    "path": path,
                    "duration_seconds": round(time.time() - started, 3),
                }
            )

        payload = {
            "mutation_id": candidate.mutation_id,
            "applied_at": utc_now_iso(),
            "touched_files": sorted(set(touched)),
            "operations": op_results,
        }
        return payload

    # ------------------------------------------------------------------
    # validation
    # ------------------------------------------------------------------
    def _validate_python_compile(self, path: Path) -> ValidationCheck:
        try:
            py_compile.compile(str(path), doraise=True)
            return ValidationCheck(name="python_compile", ok=True, details={"path": str(path)})
        except Exception as exc:  # noqa: BLE001
            return ValidationCheck(name="python_compile", ok=False, details={"path": str(path), "error": str(exc)})

    def _validate_powershell_parse(self, path: Path) -> ValidationCheck:
        command = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            (
                "$raw = Get-Content -LiteralPath '" + str(path).replace("'", "''") + "' -Raw -Encoding UTF8; "
                "$errs = $null; "
                "[void][System.Management.Automation.PSParser]::Tokenize($raw, [ref]$errs); "
                "if ($errs -and $errs.Count -gt 0) { "
                "  $errs | Select-Object -First 5 | ForEach-Object { "
                "    Write-Output ('Line ' + $_.Token.StartLine + ', Col ' + $_.Token.StartColumn + ': ' + $_.Message) "
                "  }; "
                "  exit 1 "
                "} else { exit 0 }"
            ),
        ]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=60)
            if completed.returncode == 0:
                return ValidationCheck(name="powershell_parse", ok=True, details={"path": str(path)})
            return ValidationCheck(
                name="powershell_parse",
                ok=False,
                details={"path": str(path), "stdout": completed.stdout, "stderr": completed.stderr},
            )
        except Exception as exc:  # noqa: BLE001
            return ValidationCheck(name="powershell_parse", ok=False, details={"path": str(path), "error": str(exc)})

    def _validate_file_exists(self, path: Path) -> ValidationCheck:
        return ValidationCheck(
            name="file_exists",
            ok=path.exists(),
            details={"path": str(path), "exists": path.exists()},
        )

    def _validate_file_nonempty(self, path: Path) -> ValidationCheck:
        exists = path.exists()
        size = path.stat().st_size if exists else 0
        return ValidationCheck(
            name="file_nonempty",
            ok=(exists and size > 0),
            details={"path": str(path), "size": size},
        )

    def _validate_health(self, endpoint: str) -> ValidationCheck:
        url = self.base_url + endpoint
        try:
            with urlopen(url, timeout=15) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return ValidationCheck(
                    name=f"health:{endpoint}",
                    ok=200 <= resp.status < 300,
                    details={"url": url, "status": resp.status, "body_excerpt": body[:500]},
                )
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            return ValidationCheck(name=f"health:{endpoint}", ok=False, details={"url": url, "error": str(exc)})

    def validate_candidate(self, candidate: MutationCandidate, touched_files: Sequence[str]) -> List[ValidationCheck]:
        checks: List[ValidationCheck] = []

        for file_path in touched_files:
            path = Path(file_path)
            checks.append(self._validate_file_exists(path))
            checks.append(self._validate_file_nonempty(path))

            if path.suffix.lower() == ".py":
                checks.append(self._validate_python_compile(path))
            elif path.suffix.lower() == ".ps1":
                checks.append(self._validate_powershell_parse(path))

        checks.append(self._validate_health("/health"))
        checks.append(self._validate_health("/api/ai/health"))
        checks.append(self._validate_health("/api/autonomy/health"))

        return checks

    # ------------------------------------------------------------------
    # execution orchestration
    # ------------------------------------------------------------------
    def execute_candidate(self, candidate: MutationCandidate) -> MutationResult:
        started = time.time()
        started_at = utc_now_iso()

        self._log("info", f"execute_candidate start mutation_id={candidate.mutation_id}")

        plan = self.assess_candidate(candidate)
        if not plan["allowed"]:
            finished_at = utc_now_iso()
            result = MutationResult(
                mutation_id=candidate.mutation_id,
                status="blocked",
                apply_status="not_started",
                validation_status="not_started",
                rollback_status="not_needed",
                touched_files=[],
                checks=[],
                final_outcome="rejected",
                reason="; ".join(plan["reasons"]) or "blocked_by_gate",
                started_at=started_at,
                finished_at=finished_at,
                duration_seconds=round(time.time() - started, 3),
            )
            self._write_json(self.rejected_dir / f"{candidate.mutation_id}.json", asdict(result))
            return result

        snapshot = self.create_snapshot(candidate)
        apply_payload: Optional[Dict[str, Any]] = None
        validation_checks: List[ValidationCheck] = []
        rollback_payload: Optional[Dict[str, Any]] = None

        try:
            apply_payload = self.apply_candidate(candidate)
            validation_checks = self.validate_candidate(candidate, apply_payload["touched_files"])
            all_ok = all(ch.ok for ch in validation_checks)

            validation_record = {
                "mutation_id": candidate.mutation_id,
                "checks": [asdict(ch) for ch in validation_checks],
                "validated_at": utc_now_iso(),
                "ok": all_ok,
            }
            self._write_json(self.validation_dir / f"{candidate.mutation_id}.json", validation_record)

            if not all_ok:
                rollback_payload = self.rollback_snapshot(snapshot, reason="validation_failed")
                result = MutationResult(
                    mutation_id=candidate.mutation_id,
                    status="completed",
                    apply_status="applied",
                    validation_status="failed",
                    rollback_status="rolled_back" if rollback_payload["ok"] else "rollback_failed",
                    touched_files=apply_payload["touched_files"],
                    checks=validation_checks,
                    final_outcome="rejected",
                    reason="validation_failed",
                    started_at=started_at,
                    finished_at=utc_now_iso(),
                    duration_seconds=round(time.time() - started, 3),
                )
                self._write_json(self.rejected_dir / f"{candidate.mutation_id}.json", self._result_payload(result, snapshot, apply_payload, rollback_payload))
                return result

            result = MutationResult(
                mutation_id=candidate.mutation_id,
                status="completed",
                apply_status="applied",
                validation_status="passed",
                rollback_status="not_needed",
                touched_files=apply_payload["touched_files"],
                checks=validation_checks,
                final_outcome="accepted",
                reason="validation_passed",
                started_at=started_at,
                finished_at=utc_now_iso(),
                duration_seconds=round(time.time() - started, 3),
            )
            self._write_json(self.accepted_dir / f"{candidate.mutation_id}.json", self._result_payload(result, snapshot, apply_payload, rollback_payload))
            return result

        except Exception as exc:  # noqa: BLE001
            self._log("error", f"execute_candidate failed mutation_id={candidate.mutation_id} error={exc}")
            rollback_payload = self.rollback_snapshot(snapshot, reason=f"apply_exception:{exc}")
            result = MutationResult(
                mutation_id=candidate.mutation_id,
                status="failed",
                apply_status="failed" if apply_payload is None else "partial_failure",
                validation_status="not_started" if not validation_checks else "partial",
                rollback_status="rolled_back" if rollback_payload["ok"] else "rollback_failed",
                touched_files=[] if apply_payload is None else apply_payload["touched_files"],
                checks=validation_checks,
                final_outcome="rejected",
                reason=str(exc),
                started_at=started_at,
                finished_at=utc_now_iso(),
                duration_seconds=round(time.time() - started, 3),
            )
            self._write_json(self.rejected_dir / f"{candidate.mutation_id}.json", self._result_payload(result, snapshot, apply_payload, rollback_payload))
            return result

    def _result_payload(
        self,
        result: MutationResult,
        snapshot: MutationSnapshot,
        apply_payload: Optional[Dict[str, Any]],
        rollback_payload: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return {
            "result": asdict(result),
            "snapshot": asdict(snapshot),
            "apply": apply_payload,
            "rollback": rollback_payload,
            "created_at": utc_now_iso(),
        }

    # ------------------------------------------------------------------
    # metrics / summaries
    # ------------------------------------------------------------------
    def collect_metrics(self) -> Dict[str, Any]:
        accepted = list(self.accepted_dir.glob("*.json"))
        rejected = list(self.rejected_dir.glob("*.json"))
        validation = list(self.validation_dir.glob("*.json"))
        rollback = list(self.rollback_dir.glob("*.json"))

        metrics = {
            "accepted_count": len(accepted),
            "rejected_count": len(rejected),
            "validation_count": len(validation),
            "rollback_count": len(rollback),
            "artifacts_root": str(self.artifacts_root),
            "collected_at": utc_now_iso(),
        }
        self._write_json(self.runtime_dir / "metrics_summary.json", metrics)
        return metrics