from __future__ import annotations

import asyncio
import json
import subprocess
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .store import JsonStore, utc_now_iso


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, Dict[str, Any]] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        tools = [
            {"tool_id":"file_write","name":"Write File","category":"file","capabilities":["file.write","artifact.create"],"risk_level":"guarded","supports_dry_run":False,"supports_idempotency":True,"description":"Write or replace a file inside the project sandbox.","input_schema":{"path":"string","content":"string"}},
            {"tool_id":"file_append","name":"Append File","category":"file","capabilities":["file.append"],"risk_level":"guarded","supports_dry_run":False,"supports_idempotency":False,"description":"Append text to a file inside the project sandbox.","input_schema":{"path":"string","content":"string"}},
            {"tool_id":"file_read","name":"Read File","category":"file","capabilities":["file.read"],"risk_level":"safe","supports_dry_run":False,"supports_idempotency":True,"description":"Read text from a file inside the project sandbox.","input_schema":{"path":"string"}},
            {"tool_id":"file_patch_text","name":"Patch Text File","category":"file","capabilities":["file.patch"],"risk_level":"guarded","supports_dry_run":True,"supports_idempotency":False,"description":"Replace text inside a file with verification.","input_schema":{"path":"string","search":"string","replace":"string","count":"int_optional"}},
            {"tool_id":"dir_list","name":"List Directory","category":"file","capabilities":["file.list"],"risk_level":"safe","supports_dry_run":False,"supports_idempotency":True,"description":"List files in a directory inside the project sandbox.","input_schema":{"path":"string_optional"}},
            {"tool_id":"json_read","name":"Read JSON","category":"data","capabilities":["json.read"],"risk_level":"safe","supports_dry_run":False,"supports_idempotency":True,"description":"Read JSON file from sandbox.","input_schema":{"path":"string"}},
            {"tool_id":"json_write","name":"Write JSON","category":"data","capabilities":["json.write"],"risk_level":"guarded","supports_dry_run":False,"supports_idempotency":True,"description":"Write JSON file in sandbox.","input_schema":{"path":"string","data":"object"}},
            {"tool_id":"http_get","name":"HTTP GET","category":"http","capabilities":["http.get","api.call"],"risk_level":"guarded","supports_dry_run":False,"supports_idempotency":True,"description":"Perform an HTTP GET request.","input_schema":{"url":"string","timeout_seconds":"int_optional"}},
            {"tool_id":"http_post_json","name":"HTTP POST JSON","category":"http","capabilities":["http.post","api.call"],"risk_level":"guarded","supports_dry_run":False,"supports_idempotency":False,"description":"Perform an HTTP POST JSON request.","input_schema":{"url":"string","data":"object","timeout_seconds":"int_optional"}},
            {"tool_id":"process_run","name":"Run Process","category":"process","capabilities":["process.run","command.exec"],"risk_level":"guarded","supports_dry_run":False,"supports_idempotency":False,"description":"Run a local command and capture output.","input_schema":{"command":"list[str]","cwd":"string_optional","timeout_seconds":"int_optional"}},
            {"tool_id":"powershell_script","name":"Run PowerShell Script","category":"process","capabilities":["powershell.run"],"risk_level":"guarded","supports_dry_run":False,"supports_idempotency":False,"description":"Run a PowerShell script block.","input_schema":{"script":"string","timeout_seconds":"int_optional"}},
            {"tool_id":"python_test","name":"Run Python Tests","category":"code","capabilities":["python.test","code.verify"],"risk_level":"guarded","supports_dry_run":False,"supports_idempotency":True,"description":"Run pytest quietly from the project root.","input_schema":{"args":"list[str]_optional","timeout_seconds":"int_optional"}},
            {"tool_id":"process_inspect_port","name":"Inspect Port Owner","category":"process","capabilities":["process.port.inspect"],"risk_level":"safe","supports_dry_run":False,"supports_idempotency":True,"description":"Inspect a Windows port owner.","input_schema":{"port":"int"}},
            {"tool_id":"process_kill_port","name":"Kill Port Owner","category":"process","capabilities":["process.port.kill"],"risk_level":"destructive","supports_dry_run":False,"supports_idempotency":False,"description":"Kill process holding a port.","input_schema":{"port":"int"}},
            {"tool_id":"mission_continue","name":"Continue Mission","category":"supervisor","capabilities":["mission.continue"],"risk_level":"guarded","supports_dry_run":False,"supports_idempotency":False,"description":"Continue a mission through supervisor execution layer.","input_schema":{"mission_id":"string","reason":"string_optional"}},
        ]
        for tool in tools:
            self.register(tool)

    def register(self, meta: Dict[str, Any]) -> None:
        self._tools[meta["tool_id"]] = meta

    def get(self, tool_id: str) -> Dict[str, Any]:
        if tool_id not in self._tools:
            raise ValueError(f"Unknown tool: {tool_id}")
        return self._tools[tool_id]

    def list_tools(self) -> List[Dict[str, Any]]:
        return list(self._tools.values())


class ToolExecutor:
    def __init__(
        self,
        store: JsonStore,
        event_bus: Any,
        registry: ToolRegistry,
        autonomy_getter: Callable[[], Dict[str, Any]],
    ) -> None:
        self.store = store
        self.event_bus = event_bus
        self.registry = registry
        self.autonomy_getter = autonomy_getter
        self.executions_file = self.store.root / "tool_executions.jsonl"

    def _project_root(self) -> Path:
        return Path.cwd()

    def _safe_path(self, user_path: str) -> Path:
        root = self._project_root().resolve()
        path = (root / user_path).resolve()
        if root not in path.parents and path != root:
            raise ValueError("Path escapes project root")
        return path

    def _append_execution(self, record: Dict[str, Any]) -> None:
        self.store.append_jsonl(self.executions_file, record)

    def list_executions(self, limit: int = 50, tool_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if not self.executions_file.exists():
            return []
        lines = self.executions_file.read_text(encoding="utf-8").splitlines()
        result: List[Dict[str, Any]] = []
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue
            if tool_id and item.get("tool_id") != tool_id:
                continue
            result.append(item)
            if len(result) >= limit:
                break
        result.reverse()
        return result

    def _verify_result(self, tool_id: str, payload: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
        if tool_id in ("file_write", "file_append", "json_write"):
            path = Path(result["path"])
            return {"verified": path.exists(), "check": "path_exists", "path": str(path)}

        if tool_id == "file_read":
            return {"verified": "content" in result, "check": "has_content"}

        if tool_id == "file_patch_text":
            return {"verified": result.get("replaced_count", 0) > 0, "check": "replaced_count_positive"}

        if tool_id in ("process_run", "powershell_script", "python_test"):
            return {"verified": result.get("returncode") == 0, "check": "returncode_zero"}

        if tool_id in ("http_get", "http_post_json"):
            code = result.get("status_code")
            return {"verified": isinstance(code, int) and 200 <= code < 300, "check": "http_2xx"}

        if tool_id == "process_inspect_port":
            return {"verified": result.get("returncode") == 0, "check": "inspect_ok"}

        if tool_id == "mission_continue":
            return {"verified": result.get("ok", False) is True, "check": "mission_continue_ok"}

        return {"verified": True, "check": "default_true"}

    async def execute(self, tool_id: str, payload: Dict[str, Any], requested_by: str = "api") -> Dict[str, Any]:
        meta = self.registry.get(tool_id)
        execution_id = f"tex_{uuid.uuid4().hex[:12]}"
        started_at = utc_now_iso()

        self.event_bus.publish(
            "tool_execution_started",
            payload={"execution_id": execution_id, "tool_id": tool_id, "requested_by": requested_by},
            source="tool_executor",
        )

        try:
            if tool_id == "file_write":
                result = await asyncio.to_thread(self._tool_file_write, payload)
            elif tool_id == "file_append":
                result = await asyncio.to_thread(self._tool_file_append, payload)
            elif tool_id == "file_read":
                result = await asyncio.to_thread(self._tool_file_read, payload)
            elif tool_id == "file_patch_text":
                result = await asyncio.to_thread(self._tool_file_patch_text, payload)
            elif tool_id == "dir_list":
                result = await asyncio.to_thread(self._tool_dir_list, payload)
            elif tool_id == "json_read":
                result = await asyncio.to_thread(self._tool_json_read, payload)
            elif tool_id == "json_write":
                result = await asyncio.to_thread(self._tool_json_write, payload)
            elif tool_id == "http_get":
                result = await asyncio.to_thread(self._tool_http_get, payload)
            elif tool_id == "http_post_json":
                result = await asyncio.to_thread(self._tool_http_post_json, payload)
            elif tool_id == "process_run":
                result = await asyncio.to_thread(self._tool_process_run, payload)
            elif tool_id == "powershell_script":
                result = await asyncio.to_thread(self._tool_powershell_script, payload)
            elif tool_id == "python_test":
                result = await asyncio.to_thread(self._tool_python_test, payload)
            elif tool_id == "process_inspect_port":
                result = await asyncio.to_thread(self._tool_process_inspect_port, payload)
            elif tool_id == "process_kill_port":
                result = await asyncio.to_thread(self._tool_process_kill_port, payload)
            elif tool_id == "mission_continue":
                result = await self._tool_mission_continue(payload)
            else:
                raise ValueError(f"Unsupported tool: {tool_id}")

            verification = self._verify_result(tool_id, payload, result)

            record = {
                "execution_id": execution_id,
                "tool_id": tool_id,
                "tool_name": meta.get("name"),
                "requested_by": requested_by,
                "status": "completed",
                "started_at": started_at,
                "finished_at": utc_now_iso(),
                "payload": payload,
                "result": result,
                "verification": verification,
            }
            self._append_execution(record)

            self.event_bus.publish(
                "tool_execution_completed",
                payload={
                    "execution_id": execution_id,
                    "tool_id": tool_id,
                    "verified": verification.get("verified", False),
                },
                source="tool_executor",
            )
            return record
        except Exception as exc:
            record = {
                "execution_id": execution_id,
                "tool_id": tool_id,
                "tool_name": meta.get("name"),
                "requested_by": requested_by,
                "status": "failed",
                "started_at": started_at,
                "finished_at": utc_now_iso(),
                "payload": payload,
                "error": str(exc),
            }
            self._append_execution(record)

            self.event_bus.publish(
                "tool_execution_failed",
                payload={"execution_id": execution_id, "tool_id": tool_id, "error": str(exc)},
                severity="warning",
                source="tool_executor",
            )
            return record

    async def execute_plan(self, steps: List[Dict[str, Any]], requested_by: str = "api_plan") -> Dict[str, Any]:
        results = []
        for idx, step in enumerate(steps, start=1):
            record = await self.execute(
                tool_id=step["tool_id"],
                payload=step.get("payload", {}),
                requested_by=requested_by,
            )
            results.append({"step": idx, "tool_id": step["tool_id"], "record": record})
            if record.get("status") != "completed":
                return {"ok": False, "steps": results, "failed_step": idx}
            verification = record.get("verification") or {}
            if verification.get("verified") is False:
                return {"ok": False, "steps": results, "failed_step": idx, "reason": "verification_failed"}
        return {"ok": True, "steps": results}

    def _tool_file_write(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        path = self._safe_path(payload["path"])
        content = str(payload.get("content", ""))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {"path": str(path), "bytes": len(content.encode("utf-8"))}

    def _tool_file_append(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        path = self._safe_path(payload["path"])
        content = str(payload.get("content", ""))
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(content)
        return {"path": str(path), "bytes": len(content.encode("utf-8"))}

    def _tool_file_read(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        path = self._safe_path(payload["path"])
        text = path.read_text(encoding="utf-8")
        return {"path": str(path), "content": text, "length": len(text)}

    def _tool_file_patch_text(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        path = self._safe_path(payload["path"])
        search = str(payload["search"])
        replace = str(payload["replace"])
        count = payload.get("count")
        text = path.read_text(encoding="utf-8")
        occurrences = text.count(search)
        if occurrences == 0:
            raise ValueError("search text not found")
        if count is None:
            new_text = text.replace(search, replace)
            replaced_count = occurrences
        else:
            new_text = text.replace(search, replace, int(count))
            replaced_count = min(occurrences, int(count))
        path.write_text(new_text, encoding="utf-8")
        return {"path": str(path), "replaced_count": replaced_count, "original_occurrences": occurrences}

    def _tool_dir_list(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        user_path = payload.get("path", ".")
        path = self._safe_path(user_path)
        items = []
        for item in sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            items.append({"name": item.name, "is_dir": item.is_dir(), "path": str(item)})
        return {"path": str(path), "items": items}

    def _tool_json_read(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        path = self._safe_path(payload["path"])
        data = json.loads(path.read_text(encoding="utf-8"))
        return {"path": str(path), "data": data}

    def _tool_json_write(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        path = self._safe_path(payload["path"])
        data = payload["data"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"path": str(path), "keys": list(data.keys()) if isinstance(data, dict) else None}

    def _tool_http_get(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = str(payload["url"])
        timeout_seconds = int(payload.get("timeout_seconds", 30))
        req = urllib.request.Request(url=url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return {"url": url, "status_code": getattr(resp, "status", None), "body": body}

    def _tool_http_post_json(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = str(payload["url"])
        timeout_seconds = int(payload.get("timeout_seconds", 30))
        data = json.dumps(payload.get("data", {})).encode("utf-8")
        req = urllib.request.Request(url=url, method="POST", data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return {"url": url, "status_code": getattr(resp, "status", None), "body": body}

    def _tool_process_run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        command = payload["command"]
        cwd = payload.get("cwd")
        timeout_seconds = int(payload.get("timeout_seconds", 60))
        workdir = self._safe_path(cwd) if cwd else self._project_root()
        proc = subprocess.run(command, cwd=str(workdir), capture_output=True, text=True, timeout=timeout_seconds)
        return {"command": command, "cwd": str(workdir), "returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}

    def _tool_powershell_script(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        script = str(payload["script"])
        timeout_seconds = int(payload.get("timeout_seconds", 60))
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            cwd=str(self._project_root()),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        return {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}

    def _tool_python_test(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        args = payload.get("args", ["-q"])
        timeout_seconds = int(payload.get("timeout_seconds", 120))
        python_path = self._project_root() / ".venv" / "Scripts" / "python.exe"
        cmd = [str(python_path), "-m", "pytest"] + list(args) if python_path.exists() else ["python", "-m", "pytest"] + list(args)
        proc = subprocess.run(cmd, cwd=str(self._project_root()), capture_output=True, text=True, timeout=timeout_seconds)
        return {"command": cmd, "returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}

    def _tool_process_inspect_port(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        port = int(payload["port"])
        script = f"Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | Select-Object LocalAddress, LocalPort, OwningProcess | ConvertTo-Json -Depth 5"
        proc = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], cwd=str(self._project_root()), capture_output=True, text=True, timeout=30)
        return {"port": port, "returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}

    def _tool_process_kill_port(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        port = int(payload["port"])
        if not bool(payload.get("confirm_destructive", False)):
            raise ValueError("Destructive tool requires confirm_destructive=true")
        script = f'$p = Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique; if ($p) {{ $p | ForEach-Object {{ Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue; Write-Output "Stopped PID $_" }} }}'
        proc = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], cwd=str(self._project_root()), capture_output=True, text=True, timeout=30)
        return {"port": port, "returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}

    async def _tool_mission_continue(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        mission_id = str(payload["mission_id"])
        reason = str(payload.get("reason", "tool_mission_continue"))
        autonomy = self.autonomy_getter()
        result = await autonomy["executor"].continue_mission(mission_id=mission_id, reason=reason, payload={"source": "tool_executor"})
        return result

