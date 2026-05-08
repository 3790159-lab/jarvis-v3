param(
    [int]$Port = 8010,
    [string]$BindHost = "127.0.0.1"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

chcp 65001 | Out-Null
[Console]::InputEncoding  = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONPATH = $ProjectRoot

if (Test-Path (Join-Path $ProjectRoot ".venv\Scripts\python.exe")) {
    $PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
} else {
    $PythonExe = "python"
}

$MainFile = Join-Path $ProjectRoot "app\main.py"

Write-Host "[INFO] Using Python: $PythonExe"
& $PythonExe -m py_compile $MainFile
if ($LASTEXITCODE -ne 0) { throw "Syntax check failed for app\main.py" }

& $PythonExe -c "import aiohttp; import fastapi; import uvicorn; print('IMPORTS_OK')"
if ($LASTEXITCODE -ne 0) { throw "Required backend packages are missing" }

Write-Host ("[INFO] Starting backend on {0}:{1}" -f $BindHost, $Port)
& $PythonExe -m uvicorn app.main:app --host $BindHost --port $Port
