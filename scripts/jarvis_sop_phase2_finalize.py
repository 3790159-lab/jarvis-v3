import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path
from urllib import request, error

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

# ---------------------------------------------------------
# helpers
# ---------------------------------------------------------
def _load_env(project_root: Path):
    if load_dotenv:
        load_dotenv(project_root / ".env", override=False)

def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8", "utf-8-sig", "cp1251", "cp866", "latin-1"):
        try:
            return raw.decode(enc)
        except Exception:
            pass
    return raw.decode("utf-8", errors="ignore")

def _write_text(path: Path, text: str):
    path.write_text(text, encoding="utf-8", newline="")

def _normalize_text(text: str) -> str:
    if not text:
        return ""
    replacements = {
        "â€”": "—",
        "â€“": "–",
        "â€˜": "‘",
        "â€™": "’",
        "â€œ": "“",
        "â€": "”",
        "â€¦": "…",
        "â‰¥": "≥",
        "â‰¤": "≤",
        "Â ": " ",
        "Â": "",
        "\u00a0": " ",
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"

def _extract_section(text: str, heading: str) -> str:
    pattern = re.compile(
        rf"(?ims)^\s*{re.escape(heading)}\s*$\n?(.*?)(?=^\s*##\s+|\Z)"
    )
    m = pattern.search(text)
    return m.group(1).strip() if m else ""

def _specificity_score(text: str) -> int:
    if not text:
        return 0
    score = 0
    patterns = [
        r"`[^`]+`",                       # inline code / commands / paths
        r"/[A-Za-z0-9_\-<>]+",            # slash commands / endpoints
        r"[A-Za-z]:\\",                   # windows paths
        r"\b\d+\b",                       # concrete numbers
        r"\bapproval_id\b|\bmission_id\b|\bpayload_json\b",
        r"\b\.db\b|\b\.jsonl\b|\b\.md\b|\b\.log\b",
        r"\bguarded\b|\bhigh_risk\b|\bforbidden\b",
        r"\|.*\|",                        # markdown table rows
    ]
    for p in patterns:
        score += len(re.findall(p, text, flags=re.I | re.M))
    return score

def _looks_generic(text: str) -> bool:
    bad_fragments = [
        "daily/periodic cleanings",
        "some level of other teams",
        "[Note]",
        "real-life tasks",
        "do similarly for retrying",
        "at some intervals",
    ]
    lowered = text.lower()
    return any(x.lower() in lowered for x in bad_fragments)

def _review_is_usable(review_text: str, draft_text: str) -> bool:
    if not review_text:
        return False
    if "## Review Notes" not in review_text or "## Final Revised SOP" not in review_text:
        return False
    final_part = _extract_section(review_text, "## Final Revised SOP")
    if len(final_part) < 700:
        return False
    if _looks_generic(final_part):
        return False
    review_score = _specificity_score(final_part)
    draft_score = _specificity_score(draft_text)
    if review_score < max(12, int(draft_score * 0.75)):
        return False
    return True

def _deterministic_merge(draft_text: str, review_text: str) -> str:
    draft_text = _normalize_text(draft_text)
    review_notes = _extract_section(review_text, "## Review Notes")
    final_part = _extract_section(review_text, "## Final Revised SOP")

    # Anti-degradation rule:
    # keep draft as source of truth unless review is clearly stronger.
    if final_part and not _looks_generic(final_part):
        if _specificity_score(final_part) >= int(_specificity_score(draft_text) * 0.95):
            candidate = _normalize_text(final_part)
            if len(candidate) >= int(len(draft_text) * 0.80):
                return candidate

    if review_notes:
        notes_block = (
            "# Review Notes (retained from review stage)\n\n"
            + _normalize_text(review_notes)
            + "\n\n---\n\n"
        )
        return _normalize_text(notes_block + draft_text)

    return draft_text

def _call_openai_compatible(base_url: str, api_key: str, model: str, prompt: str, timeout_sec: int):
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a senior operations editor. "
                    "Preserve specificity. Do not generalize. "
                    "Keep concrete commands, file names, thresholds, risk levels, "
                    "artifact paths, APIs, and operator steps unless clearly incorrect."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }

    data = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with request.urlopen(req, timeout=timeout_sec) as resp:
        raw = json.loads(resp.read().decode("utf-8"))
    return raw["choices"][0]["message"]["content"]

def _build_uplift_prompt(topic: str, draft_text: str, review_text: str) -> str:
    return f"""
Task: Produce the final production-ready SOP for this topic:
{topic}

Rules:
1. Use the Claude draft as the source of truth.
2. Use the review only when it improves clarity without reducing specificity.
3. Never make the SOP more generic than the draft.
4. Preserve concrete commands, file names, paths, risk levels, thresholds, APIs, databases, logs, and artifact locations from the draft unless clearly wrong.
5. Remove weak filler, vague recommendations, and generic operational advice.
6. Normalize typography and markdown.
7. Output ONLY the final SOP in markdown. No intro, no commentary.

Claude draft:
---
{draft_text}
---

Review output:
---
{review_text}
---
""".strip()

# ---------------------------------------------------------
# main
# ---------------------------------------------------------
def main():
    project_root = Path(sys.argv[1]).resolve()
    _load_env(project_root)

    runs_root = project_root / "jarvis_stage3_artifacts" / "real_runs"
    if not runs_root.exists():
        raise SystemExit("real_runs folder not found")

    candidates = sorted(
        [p for p in runs_root.glob("sop_*") if p.is_dir()],
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )
    if not candidates:
        raise SystemExit("No SOP run folders found")

    run_dir = candidates[0]
    claude_path = run_dir / "claude_draft.md"
    review_path = run_dir / "openai_review.md"
    final_path = run_dir / "final_sop.md"
    phase2_path = run_dir / "final_sop_phase2.md"
    report_path = run_dir / "phase2_report.txt"

    if not claude_path.exists():
        raise SystemExit("claude_draft.md not found")

    draft_text = _normalize_text(_read_text(claude_path))
    review_text = _normalize_text(_read_text(review_path)) if review_path.exists() else ""
    old_final = _normalize_text(_read_text(final_path)) if final_path.exists() else ""

    topic = "Telegram workflow for handling real Jarvis operator requests"

    prefer_real = os.getenv("OPENAI_REVIEW_PREFER_REAL", "true").strip().lower() in {"1", "true", "yes", "on"}
    real_base = os.getenv("OPENAI_REVIEW_REAL_BASE_URL", "https://api.openai.com/v1").strip()
    real_model = os.getenv("OPENAI_REVIEW_REAL_MODEL", "gpt-5.4").strip()
    real_key = (
        os.getenv("OPENAI_REVIEW_REAL_API_KEY", "").strip()
        or os.getenv("OPENAI_API_KEY", "").strip()
    )

    timeout_sec = int(os.getenv("OPENAI_REVIEW_TIMEOUT_SECONDS", "240").strip())

    method = "deterministic_merge"
    result_text = ""
    remote_error = ""

    if prefer_real and real_key:
        try:
            prompt = _build_uplift_prompt(topic=topic, draft_text=draft_text, review_text=review_text)
            result_text = _call_openai_compatible(
                base_url=real_base,
                api_key=real_key,
                model=real_model,
                prompt=prompt,
                timeout_sec=timeout_sec,
            )
            result_text = _normalize_text(result_text)
            method = f"real_openai:{real_model}"
        except Exception as e:
            remote_error = str(e)

    if not result_text:
        # keep deterministic, draft-preserving path
        usable_review = _review_is_usable(review_text, draft_text)
        if usable_review:
            method = "deterministic_merge:review_usable"
        else:
            method = "deterministic_merge:draft_preserved"
        result_text = _deterministic_merge(draft_text, review_text)

    # Safety gate: never let final become much weaker than the draft
    if _specificity_score(result_text) < int(_specificity_score(draft_text) * 0.85):
        method += " -> fallback_to_claude_draft"
        result_text = draft_text

    if final_path.exists():
        backup_path = final_path.with_suffix(".pre_phase2.bak")
        if not backup_path.exists():
            backup_path.write_text(old_final, encoding="utf-8", newline="")

    _write_text(phase2_path, result_text)
    _write_text(final_path, result_text)

    report = {
        "run_dir": str(run_dir),
        "method": method,
        "remote_error": remote_error,
        "draft_specificity": _specificity_score(draft_text),
        "result_specificity": _specificity_score(result_text),
        "review_usable": _review_is_usable(review_text, draft_text),
        "final_path": str(final_path),
        "phase2_path": str(phase2_path),
        "timestamp": int(time.time()),
    }

    _write_text(report_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    print(json.dumps(report, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
