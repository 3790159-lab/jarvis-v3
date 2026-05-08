param(
    [string]$ProjectRoot = (Get-Location).Path,
    [int]$Port = 8015
)

$ErrorActionPreference = "Stop"

[Console]::InputEncoding  = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
chcp 65001 | Out-Null

Set-Location $ProjectRoot

if (Test-Path ".\.venv\Scripts\python.exe") {
    $Py = (Resolve-Path ".\.venv\Scripts\python.exe").Path
} else {
    $Py = "python"
}

$DataDir = Join-Path $ProjectRoot "data"
$DbPath = Join-Path $DataDir "jarvis_runtime.db"
New-Item -ItemType Directory -Path $DataDir -Force | Out-Null

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:APP_HOST = "127.0.0.1"
$env:APP_PORT = "$Port"
$env:BACKEND_BASE_URL = "http://127.0.0.1:$Port"
$env:DATABASE_PATH = $DbPath
$env:PYTHONPATH = $ProjectRoot

$startCode = @"
import os
import sys
import traceback

try:
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["PYTHONUTF8"] = "1"
    os.environ["APP_HOST"] = "127.0.0.1"
    os.environ["APP_PORT"] = "$Port"
    os.environ["BACKEND_BASE_URL"] = "http://127.0.0.1:$Port"
    os.environ["DATABASE_PATH"] = r"$DbPath"
    os.environ["PYTHONPATH"] = r"$ProjectRoot"

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    import uvicorn
    import app.main  # noqa

    uvicorn.run("app.main:app", host="127.0.0.1", port=int("$Port"))
except Exception:
    print("FATAL_STARTUP_ERROR")
    traceback.print_exc()
    raise
"@

$tmp = Join-Path $env:TEMP "jarvis_backend_start_direct_runtime.py"
[System.IO.File]::WriteAllText($tmp, $startCode, [System.Text.UTF8Encoding]::new($false))

& $Py $tmp