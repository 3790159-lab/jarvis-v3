from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


DEFAULT_PATTERNS = ["*.md", "*.txt", "*.json", "*.log"]


def _decode_best_effort(raw: bytes) -> tuple[str, str]:
    encodings = [
        "utf-8-sig",
        "utf-8",
        "cp1251",
        "cp1252",
        "latin-1",
    ]
    for enc in encodings:
        try:
            return raw.decode(enc), enc
        except Exception:
            pass
    return raw.decode("utf-8", errors="replace"), "utf-8-replace"


def _score_text(text: str) -> int:
    bad_markers = [
        "�",
        "Ð",
        "Ñ",
        "â€",
        "â€™",
        "â€œ",
        "â€\x9d",
        "Â",
    ]
    score = 0
    score -= text.count("\ufffd") * 20
    for marker in bad_markers:
        score -= text.count(marker) * 5
    score += sum(1 for ch in text if ch.isprintable() or ch in "\r\n\t")
    return score


def _repair_mojibake(text: str) -> str:
    candidates = [text]

    for src_enc in ("latin-1", "cp1252"):
        try:
            candidates.append(text.encode(src_enc, errors="ignore").decode("utf-8", errors="ignore"))
        except Exception:
            pass

    best = max(candidates, key=_score_text)
    return best


def normalize_text(text: str) -> str:
    text = _repair_mojibake(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def normalize_file(path: Path, dry_run: bool = False) -> dict:
    raw = path.read_bytes()
    decoded, encoding_used = _decode_best_effort(raw)
    normalized = normalize_text(decoded)

    original_utf8: str
    try:
        original_utf8 = raw.decode("utf-8")
    except Exception:
        original_utf8 = decoded

    changed = normalized != original_utf8

    if changed and not dry_run:
        path.write_text(normalized, encoding="utf-8", newline="\n")

    return {
        "path": str(path),
        "encoding_used": encoding_used,
        "changed": changed,
        "size_before": len(raw),
        "size_after": len(normalized.encode("utf-8")),
    }


def expand_targets(paths: list[Path], dirs: list[Path], patterns: list[str], recursive: bool) -> list[Path]:
    results: list[Path] = []

    for p in paths:
        if p.exists() and p.is_file():
            results.append(p)

    glob_method = "rglob" if recursive else "glob"

    for d in dirs:
        if not d.exists() or not d.is_dir():
            continue
        for pattern in patterns:
            iterator = getattr(d, glob_method)(pattern)
            for p in iterator:
                if p.is_file():
                    results.append(p)

    unique: list[Path] = []
    seen = set()
    for p in results:
        key = str(p.resolve()) if p.exists() else str(p)
        if key not in seen:
            seen.add(key)
            unique.append(p)

    return unique


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Normalize text artifacts to UTF-8.")
    parser.add_argument("paths", nargs="*", help="File paths to normalize.")
    parser.add_argument("--path", action="append", default=[])
    parser.add_argument("--file", action="append", default=[])
    parser.add_argument("--files", nargs="*", default=[])
    parser.add_argument("--dir", action="append", default=[])
    parser.add_argument("--root", action="append", default=[])
    parser.add_argument("--run-dir", action="append", default=[])
    parser.add_argument("--pattern", action="append", default=[])
    parser.add_argument("--glob", action="append", default=[])
    parser.add_argument("--ext", action="append", default=[])
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args, unknown = parser.parse_known_args()

    file_args = list(args.paths) + list(args.path) + list(args.file) + list(args.files)
    dir_args = list(args.dir) + list(args.root) + list(args.run_dir)

    patterns = []
    patterns.extend(args.pattern)
    patterns.extend(args.glob)
    patterns.extend([f"*.{ext.lstrip('.')}" for ext in args.ext])

    if not patterns:
        patterns = DEFAULT_PATTERNS

    file_paths = [Path(x) for x in file_args if x]
    dir_paths = [Path(x) for x in dir_args if x]

    targets = expand_targets(file_paths, dir_paths, patterns, recursive=args.recursive)

    if not targets:
        payload = {
            "ok": True,
            "message": "No target files found; nothing to normalize.",
            "targets": [],
            "ignored_unknown_args": unknown,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    results = []
    failures = []

    for target in targets:
        try:
            results.append(normalize_file(target, dry_run=args.dry_run))
        except Exception as exc:
            failures.append({"path": str(target), "error": str(exc)})

    payload = {
        "ok": len(failures) == 0,
        "dry_run": bool(args.dry_run),
        "normalized_count": sum(1 for x in results if x["changed"]),
        "processed_count": len(results),
        "failed_count": len(failures),
        "results": results,
        "failures": failures,
        "ignored_unknown_args": unknown,
    }

    if not args.quiet:
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    return 0 if len(failures) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
