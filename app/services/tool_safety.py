from typing import Dict, Any

MAX_OUTPUT = 5000

RETRYABLE_ERRORS = [
    "timeout",
    "connection",
    "temporarily",
    "429",
    "502",
    "503",
    "504",
]

def is_retryable(error: str) -> bool:
    if not error:
        return False
    err = error.lower()
    return any(x in err for x in RETRYABLE_ERRORS)

def _truncate(value: str) -> str:
    if not isinstance(value, str):
        return value
    if len(value) <= MAX_OUTPUT:
        return value
    return value[:MAX_OUTPUT] + "\n...[truncated]..."

def limit_output(data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(data, dict):
        return data
    out = data.copy()
    if "stdout" in out:
        out["stdout"] = _truncate(out["stdout"])
    if "stderr" in out:
        out["stderr"] = _truncate(out["stderr"])
    if "body" in out and isinstance(out["body"], str):
        out["body"] = _truncate(out["body"])
    return out

def check_tool_allowed(tool: str, metadata: Dict[str, Any]) -> None:
    allowed = metadata.get("allowed_tools")
    if allowed and tool not in allowed:
        raise Exception(f"Tool '{tool}' is not allowed for this step")

def check_execution_mode(tool: str, metadata: Dict[str, Any]) -> None:
    mode = str(metadata.get("mode", "full")).strip().lower()
    if mode == "safe" and tool in ["shell", "python", "http"]:
        raise Exception(f"Tool '{tool}' is blocked in safe mode")
    if mode == "restricted" and tool in ["shell"]:
        raise Exception("Shell tool is blocked in restricted mode")