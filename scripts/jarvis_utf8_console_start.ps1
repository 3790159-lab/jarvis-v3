param(
    [string]$ProjectRoot = (Get-Location).Path,
    [int]$Port = 8015
)

$ErrorActionPreference = "Stop"

[Console]::InputEncoding  = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
chcp 65001 | Out-Null
$PSDefaultParameterValues['Out-File:Encoding']    = 'utf8'
$PSDefaultParameterValues['Set-Content:Encoding'] = 'utf8'
$PSDefaultParameterValues['Add-Content:Encoding'] = 'utf8'

Set-Location $ProjectRoot

if (Test-Path ".\.venv\Scripts\python.exe") {
    $Py = (Resolve-Path ".\.venv\Scripts\python.exe").Path
} else {
    $Py = "python"
}

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:APP_HOST = "127.0.0.1"
$env:APP_PORT = "$Port"
$env:BACKEND_BASE_URL = "http://127.0.0.1:$Port"
$env:PYTHONPATH = $ProjectRoot

$startCode = @"
import os
import sys
import uvicorn

os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"
os.environ["APP_HOST"] = "127.0.0.1"
os.environ["APP_PORT"] = "$Port"
os.environ["BACKEND_BASE_URL"] = "http://127.0.0.1:$Port"
os.environ["PYTHONPATH"] = r"$ProjectRoot"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

uvicorn.run("app.main:app", host="127.0.0.1", port=int("$Port"))
"@

$tmp = Join-Path $env:TEMP "jarvis_utf8_runtime_start.py"
[System.IO.File]::WriteAllText($tmp, $startCode, [System.Text.UTF8Encoding]::new($false))

& $Py $tmp