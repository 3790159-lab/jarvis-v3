import pathlib
import re
import sys

path = pathlib.Path(r"""C:\\Users\\Daniil Lapin\\Downloads\\supervisor_v1_5_smart_telegram (1)\\supervisor_v1_5_smart_telegram\\app\\api\\cloud_control.py""")
text = path.read_text(encoding="utf-8")

def ensure_imports(src: str) -> str:
    lines = src.splitlines()
    insert_at = 0
    while insert_at < len(lines) and lines[insert_at].startswith("from __future__ import"):
        insert_at += 1

    need = []
    required = [
        "import re",
        "import json",
        "from pathlib import Path",
        "from typing import Any",
    ]
    for item in required:
        if item not in src:
            need.append(item)

    if need:
        lines[insert_at:insert_at] = need + [""]
        src = "\n".join(lines)
        if not src.endswith("\n"):
            src += "\n"
    return src

def inject_local_import(src: str, func_name: str, import_block: str) -> str:
    pattern = re.compile(rf"(def {func_name}\([^\n]*\):\n)")
    m = pattern.search(src)
    if not m:
        return src

    start = m.end()
    if src[start:start+len(import_block)] == import_block:
        return src

    return src[:start] + import_block + src[start:]

text = ensure_imports(text)

text = inject_local_import(
    text,
    "_extract_cloud_codegen_text",
    "    import re\n"
)

text = inject_local_import(
    text,
    "_write_cloud_codegen_debug",
    "    import json\n    from pathlib import Path\n"
)

path.write_text(text, encoding="utf-8")
print("cloud_control.py hard-fixed")