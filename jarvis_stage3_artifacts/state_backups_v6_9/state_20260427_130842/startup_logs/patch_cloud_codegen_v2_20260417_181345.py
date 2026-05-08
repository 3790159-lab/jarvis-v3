import pathlib
import re
import sys

path = pathlib.Path(r"""C:\\Users\\Daniil Lapin\\Downloads\\supervisor_v1_5_smart_telegram (1)\\supervisor_v1_5_smart_telegram\\app\\api\\cloud_control.py""")
text = path.read_text(encoding="utf-8")

marker = "SAFE_CLOUD_CODEGEN_V2"

helper_block = r'''
# SAFE_CLOUD_CODEGEN_V2

def _collect_codegen_strings(value, sink):
    try:
        if value is None:
            return
        if isinstance(value, str):
            v = value.strip()
            if v:
                sink.append(v)
            return
        if isinstance(value, dict):
            for v in value.values():
                _collect_codegen_strings(v, sink)
            return
        if isinstance(value, (list, tuple, set)):
            for v in value:
                _collect_codegen_strings(v, sink)
            return
    except Exception:
        return

def _extract_cloud_codegen_text(result: Any, language: str = "html") -> str:
    direct = _normalize_generated_text(_extract_agent_text_any(result))
    if direct and (not _looks_like_planner_text(direct)) and _looks_like_code(direct, language):
        return direct

    strings = []
    _collect_codegen_strings(result, strings)

    for item in strings:
        text = _normalize_generated_text(item)
        if text and (not _looks_like_planner_text(text)) and _looks_like_code(text, language):
            return text

        fence_match = re.search(r"```(?:html|xml|javascript|js|python)?\s*(.*?)```", item, flags=re.IGNORECASE | re.DOTALL)
        if fence_match:
            fenced = _normalize_generated_text(fence_match.group(1))
            if fenced and (not _looks_like_planner_text(fenced)) and _looks_like_code(fenced, language):
                return fenced

        if (language or "html").lower() == "html":
            html_match = re.search(r"<!DOCTYPE html[\s\S]*?</html>|<html[\s\S]*?</html>", item, flags=re.IGNORECASE)
            if html_match:
                html_text = _normalize_generated_text(html_match.group(0))
                if html_text and (not _looks_like_planner_text(html_text)) and _looks_like_code(html_text, language):
                    return html_text

    joined = "\n\n".join(strings)
    if (language or "html").lower() == "html":
        html_match = re.search(r"<!DOCTYPE html[\s\S]*?</html>|<html[\s\S]*?</html>", joined, flags=re.IGNORECASE)
        if html_match:
            html_text = _normalize_generated_text(html_match.group(0))
            if html_text and (not _looks_like_planner_text(html_text)) and _looks_like_code(html_text, language):
                return html_text

    return ""

def _write_cloud_codegen_debug(result: Any, suffix: str = "latest") -> str:
    try:
        base = Path("state/runtime_exec").resolve()
        base.mkdir(parents=True, exist_ok=True)
        dest = base / f"cloud_codegen_debug_{suffix}.json"
        with dest.open("w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)
        return str(dest)
    except Exception:
        return ""
'''

if marker not in text:
    anchor = "def _direct_cloud_codegen(payload: dict, timeout_seconds: int) -> dict:"
    if anchor not in text:
        print("Could not locate _direct_cloud_codegen anchor.", file=sys.stderr)
        sys.exit(1)
    text = text.replace(anchor, helper_block + "\n\ndef _direct_cloud_codegen(payload: dict, timeout_seconds: int) -> dict:", 1)

pattern = re.compile(
    r"def _direct_cloud_codegen\(payload: dict, timeout_seconds: int\) -> dict:\n.*?(?=\ndef _cloud_codegen\(payload: dict, timeout_seconds: int\) -> dict:)",
    re.DOTALL
)

replacement = r'''
def _direct_cloud_codegen(payload: dict, timeout_seconds: int) -> dict:
    prompt = str(payload.get("input") or "").strip()
    language = str(payload.get("language") or "html").strip().lower()
    model = str(payload.get("model") or "").strip()

    if not prompt:
        raise ValueError("Codegen prompt is empty.")

    adapters = payload.get("cloud_adapters") or [
        "claude_code_bridge",
        "openai_compatible_http",
    ]

    errors = []
    last_result = None

    for adapter_name in adapters:
        agent_id = {
            "claude_code_bridge": "agent_claude_code_bridge_01",
            "openai_compatible_http": "agent_openai_compatible_http_01",
        }.get(adapter_name, f"agent_{adapter_name}_01")

        agent_payload = {
            "adapter_name": adapter_name,
            "agent_id": payload.get("agent_id") or agent_id,
            "input": prompt,
            "message": prompt,
            "text": prompt,
            "prompt": prompt,
            "query": prompt,
            "mode": "codegen",
            "task_type": "codegen",
            "response_format": "code_only",
            "language": language,
            "temperature": float(payload.get("temperature") or 0.2),
        }

        if model:
            agent_payload["model"] = model

        try:
            result = _call(
                "POST",
                "/api/agents/invoke",
                payload=agent_payload,
                timeout=max(int(timeout_seconds or 180), 120),
            )
            last_result = result

            text = _extract_cloud_codegen_text(result, language)
            if text:
                return {
                    "ok": True,
                    "provider": "cloud",
                    "adapter_name": adapter_name,
                    "model": model or "default",
                    "language": language,
                    "text": text,
                    "raw": result,
                }

            debug_path = _write_cloud_codegen_debug(result, f"empty_{adapter_name}")
            if debug_path:
                errors.append(f"{adapter_name}=empty_text(debug={debug_path})")
            else:
                errors.append(f"{adapter_name}=empty_text")
        except Exception as exc:
            errors.append(f"{adapter_name}={exc}")

    if last_result is not None:
        _write_cloud_codegen_debug(last_result, "last_result")

    raise RuntimeError("Cloud codegen returned empty text. " + " | ".join(errors))
'''

new_text, count = pattern.subn(replacement + "\n", text, count=1)
if count != 1:
    print("Could not replace _direct_cloud_codegen.", file=sys.stderr)
    sys.exit(1)

path.write_text(new_text, encoding="utf-8")
print("cloud_control.py patched to SAFE_CLOUD_CODEGEN_V2")