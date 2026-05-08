from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

try:
    from openpyxl import Workbook
    OPENPYXL_AVAILABLE = True
except Exception:
    Workbook = None
    OPENPYXL_AVAILABLE = False


def write_text(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def write_json(path: Path, data: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in columns})
    return path


def write_xlsx(path: Path, rows: list[dict[str, Any]], columns: list[str], sheet_name: str = "Data") -> Path | None:
    if not OPENPYXL_AVAILABLE:
        return None

    path.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name[:31] if sheet_name else "Data"

    ws.append(columns)
    for row in rows:
        ws.append([row.get(col, "") for col in columns])

    for cell in ws[1]:
        cell.font = cell.font.copy(bold=True)

    ws.freeze_panes = "A2"
    wb.save(path)
    return path


def infer_columns(preferred_order: list[str], rows: list[dict[str, Any]]) -> list[str]:
    discovered: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in discovered:
                discovered.append(key)

    ordered: list[str] = [key for key in preferred_order if key in discovered]
    remaining: list[str] = [key for key in discovered if key not in ordered]
    return ordered + remaining


def file_exists_nonempty(path: Path) -> bool:
    return path.exists() and path.is_file() and path.stat().st_size > 0
