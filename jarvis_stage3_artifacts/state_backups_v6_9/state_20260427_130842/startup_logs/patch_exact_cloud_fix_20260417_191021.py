import pathlib
import re
import sys

path = pathlib.Path(r"""C:\\Users\\Daniil Lapin\\Downloads\\supervisor_v1_5_smart_telegram (1)\\supervisor_v1_5_smart_telegram\\app\\api\\cloud_control.py""")
text = path.read_text(encoding="utf-8")

def ensure_exact_import(src: str, line: str) -> str:
    if re.search(rf"(?m)^{re.escape(line)}\s*$", src):
        return src

    m = re.search(r"(?m)^(from __future__ import .*\n)+", src)
    pos = m.end() if m else 0
    return src[:pos] + line + "\n" + src[pos:]

for line in [
    "import re",
    "import json",
    "from pathlib import Path",
    "from typing import Any",
]:
    text = ensure_exact_import(text, line)

def inject_local_block(src: str, func_name: str, block: str) -> str:
    pattern = re.compile(rf"(def {re.escape(func_name)}\([^\n]*\):\n)")
    m = pattern.search(src)
    if not m:
        raise SystemExit(f"Could not find function: {func_name}")

    start = m.end()
    window = src[start:start+300]
    if block.strip() in window:
        return src

    return src[:start] + block + src[start:]

text = inject_local_block(
    text,
    "_extract_cloud_codegen_text",
    "    import re\n"
)

text = inject_local_block(
    text,
    "_write_cloud_codegen_debug",
    "    import json\n    from pathlib import Path\n"
)

path.write_text(text, encoding="utf-8")
print("cloud_control.py exact-fixed")