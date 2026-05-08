from pathlib import Path

main_path = Path("app/main.py")
if not main_path.exists():
    raise SystemExit("app/main.py not found")

raw = main_path.read_bytes()

# remove BOM if present
if raw.startswith(b"\xef\xbb\xbf"):
    raw = raw[3:]

text = raw.decode("utf-8", errors="replace")

# normalize line endings
text = text.replace("\r\n", "\n").replace("\r", "\n")

future_line = "from __future__ import annotations"
diag_import_line = "from app.diag_router import router as diag_router"
diag_include_line = "app.include_router(diag_router)"

lines = text.split("\n")

# remove duplicate BOM chars that survived decoding
lines = [line.replace("\ufeff", "") for line in lines]

# remove existing injected import duplicates
cleaned = []
seen_diag_import = False
for line in lines:
    if line.strip() == diag_import_line:
        if seen_diag_import:
            continue
        seen_diag_import = True
    cleaned.append(line)
lines = cleaned

# ensure future import exists and is first real statement
without_future = [line for line in lines if line.strip() != future_line]

insert_index = 0
while insert_index < len(without_future) and without_future[insert_index].strip() == "":
    insert_index += 1

without_future.insert(insert_index, future_line)

# ensure diag import exists after future import block
has_diag_import = any(line.strip() == diag_import_line for line in without_future)
if not has_diag_import:
    future_idx = next(i for i, line in enumerate(without_future) if line.strip() == future_line)
    without_future.insert(future_idx + 1, diag_import_line)

text = "\n".join(without_future).rstrip() + "\n"

if diag_include_line not in text:
    text += "\ntry:\n    app.include_router(diag_router)\nexcept Exception as exc:\n    print(f\"[diag_router] include skipped: {exc}\")\n"

main_path.write_text(text, encoding="utf-8", newline="\n")
print("main.py fixed successfully")
