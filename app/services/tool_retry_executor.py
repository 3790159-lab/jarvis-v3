import time
from typing import Dict, Any

from app.services.tool_executor_runtime import execute_tool
from app.services.tool_safety import (
    is_retryable,
    limit_output,
    check_tool_allowed,
    check_execution_mode,
)

def execute_with_retry(tool: str, payload: Dict[str, Any], metadata: Dict[str, Any]) -> Dict[str, Any]:
    retries = max(0, int(metadata.get("retries", 1)))
    step_timeout = max(1, int(metadata.get("step_timeout", 30)))

    last_error = None
    started = time.time()

    for attempt in range(retries + 1):
        try:
            check_tool_allowed(tool, metadata)
            check_execution_mode(tool, metadata)

            elapsed = time.time() - started
            if elapsed > step_timeout:
                return {
                    "ok": False,
                    "tool": tool,
                    "output": None,
                    "error": "step timeout exceeded",
                    "attempt": attempt + 1,
                }

            result = execute_tool(tool, payload)
            if isinstance(result.get("output"), dict):
                result["output"] = limit_output(result.get("output"))

            result["attempt"] = attempt + 1

            if result.get("ok"):
                return result

            last_error = result.get("error")

            if not is_retryable(str(last_error or "")):
                return result

        except Exception as e:
            last_error = str(e)
            if not is_retryable(last_error):
                return {
                    "ok": False,
                    "tool": tool,
                    "output": None,
                    "error": last_error,
                    "attempt": attempt + 1,
                }

        if attempt < retries:
            time.sleep(2 ** attempt)

    return {
        "ok": False,
        "tool": tool,
        "output": None,
        "error": last_error or "retry failed",
        "attempt": retries + 1,
    }