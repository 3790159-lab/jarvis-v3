import re
import sys
import sqlite3
from pathlib import Path

project_root = Path(sys.argv[1]).resolve()
source_console = Path(sys.argv[2]).resolve()
fixed_console = Path(sys.argv[3]).resolve()
route_mode = sys.argv[4].strip()

def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8", "utf-8-sig", "cp1251", "cp866", "latin-1"):
        try:
            return raw.decode(enc)
        except Exception:
            pass
    return raw.decode("utf-8", errors="ignore")

def write_text(path: Path, text: str):
    path.write_text(text, encoding="utf-8", newline="")

text = read_text(source_console)

# remove old helpers completely
text = re.sub(r'\n?function\s+Ensure-SopPhase2Watcher\b.*?\n\}\n', '\n', text, flags=re.S)
text = re.sub(r'\n?function\s+Invoke-SopPhase2Finalize\b.*?\n\}\n', '\n', text, flags=re.S)

# remove broken direct-call remnants
text = re.sub(r'(?m)^[ \t]*Invoke-SopPhase2Finalize[^\r\n]*\r?\n?', '', text)
text = re.sub(r'(?m)^[ \t]*-ProjectRoot\s+\$ProjectRoot\s*\r?\n?', '', text)

helper = r'''
function Invoke-SopPhase2Finalize {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot
    )

    $WrapperPath = Join-Path $ProjectRoot "scripts\jarvis_sop_phase2_finalize.ps1"
    if (-not (Test-Path $WrapperPath)) {
        Write-Warning "Phase 2 wrapper not found: $WrapperPath"
        return
    }

    Write-Host ""
    Write-Host "== PHASE 2 FINALIZE ==" -ForegroundColor Cyan

    try {
        & $WrapperPath -ProjectRoot $ProjectRoot
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Phase 2 finalize returned exit code $LASTEXITCODE"
        }
    }
    catch {
        Write-Warning ("Phase 2 finalize failed: " + $_.Exception.Message)
    }
}
'''.strip("\n")

m_strict = re.search(r'(?ms)(Set-StrictMode\s+-Version\s+Latest\s*\r?\n)', text)
if m_strict:
    text = re.sub(
        r'(?ms)(Set-StrictMode\s+-Version\s+Latest\s*\r?\n)',
        r'\1' + helper + '\n\n',
        text,
        count=1
    )
else:
    text = helper + '\n\n' + text

# normalize exact menu line
text = re.sub(
    r'(?m)^\s*Write-Host\s+"8\)\s*Quick real run:.*$',
    '    Write-Host "8) Quick real run: Telegram workflow SOP + Direct Phase 2"',
    text
)

if 'Quick real run: Telegram workflow SOP + Direct Phase 2' not in text:
    text = text.replace(
        '8) Quick real run: Telegram workflow SOP',
        '8) Quick real run: Telegram workflow SOP + Direct Phase 2'
    )

# collapse duplicated label
text = re.sub(
    r'Quick real run: Telegram workflow SOP \+ Direct Phase 2(?:\s*\+\s*Direct Phase 2)+',
    'Quick real run: Telegram workflow SOP + Direct Phase 2',
    text
)

case8_pattern = r'(?ms)(^\s*["\']8["\']\s*\{)(.*?)(?=^\s*["\'](?:Q|q)["\']\s*\{|^\s*default\s*\{|^\s*\}\s*$)'
m = re.search(case8_pattern, text)
if not m:
    raise SystemExit("Could not locate option 8 block")

case_header = m.group(1)
case_body = m.group(2).rstrip("\r\n")

if 'Invoke-SopPhase2Finalize -ProjectRoot $ProjectRoot' not in case_body:
    case_body += '\n\n            Invoke-SopPhase2Finalize -ProjectRoot $ProjectRoot'

new_case = case_header + case_body + '\n'
text = text[:m.start()] + new_case + text[m.end():]

write_text(fixed_console, text)

print(f"FIXED_CONSOLE={fixed_console}")

# -------------------------------------------------
# Best-effort runtime DB sync for openai_compatible_http
# -------------------------------------------------
target_base = "https://api.openai.com/v1" if route_mode == "real_openai_gpt54" else "http://127.0.0.1:11434/v1"
target_model = "gpt-5.4" if route_mode == "real_openai_gpt54" else "llama3.2:latest"

db_changes = 0
for db in project_root.rglob("*.db"):
    if any(x in str(db) for x in [r"\.venv\\", r"\logs\\", r"\hotfix_backups\\"]):
        continue
    try:
        con = sqlite3.connect(str(db))
        cur = con.cursor()
        tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        local_changes = 0

        for table in tables:
            try:
                cols = [r[1] for r in cur.execute(f"PRAGMA table_info('{table}')")]
            except Exception:
                continue

            colset = set(cols)

            if {"name", "enabled"}.issubset(colset):
                cur.execute(f"UPDATE '{table}' SET enabled=1 WHERE lower(name)=lower(?)", ("openai_compatible_http",))
                local_changes += cur.rowcount

            if {"adapter_name", "enabled"}.issubset(colset):
                cur.execute(f"UPDATE '{table}' SET enabled=1 WHERE lower(adapter_name)=lower(?)", ("openai_compatible_http",))
                local_changes += cur.rowcount

            for text_col in [c for c in cols if c.lower() in {"payload", "config", "config_json", "metadata", "data", "value", "json"}]:
                try:
                    rows = list(cur.execute(f"SELECT rowid, {text_col} FROM '{table}' WHERE {text_col} IS NOT NULL"))
                except Exception:
                    continue

                for rowid, value in rows:
                    if not isinstance(value, str):
                        continue
                    if "openai_compatible_http" not in value and "127.0.0.1:11434/v1" not in value and "gpt-5.4" not in value:
                        continue

                    new_value = value
                    new_value = re.sub(
                        r'("name"\s*:\s*"openai_compatible_http"[\s\S]{0,400}?"enabled"\s*:\s*)false',
                        r'\1true',
                        new_value,
                        flags=re.I
                    )
                    new_value = re.sub(
                        r'("adapter_name"\s*:\s*"openai_compatible_http"[\s\S]{0,400}?"enabled"\s*:\s*)false',
                        r'\1true',
                        new_value,
                        flags=re.I
                    )
                    new_value = new_value.replace("http://127.0.0.1:11434/v1", target_base)
                    new_value = new_value.replace("llama3.2:latest", target_model)

                    if new_value != value:
                        cur.execute(f"UPDATE '{table}' SET {text_col}=? WHERE rowid=?", (new_value, rowid))
                        local_changes += cur.rowcount

        con.commit()
        con.close()

        if local_changes:
            db_changes += local_changes
            print(f"DB_PATCHED={db} :: {local_changes}")
    except Exception:
        pass

print(f"DB_PATCH_COUNT={db_changes}")
