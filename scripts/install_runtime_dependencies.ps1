param()

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

Write-Host "[INFO] Using Python: $PythonExe"
& $PythonExe -m pip install --upgrade pip setuptools wheel
if ($LASTEXITCODE -ne 0) { throw "Failed to upgrade pip/setuptools/wheel" }

$Packages = @(
    "aiohttp",
    "fastapi",
    "uvicorn",
    "requests",
    "pyTelegramBotAPI",
    "python-dotenv",
    "pydantic",
    "pydantic-settings"
)

Write-Host "[INFO] Installing runtime packages..."
& $PythonExe -m pip install @Packages
if ($LASTEXITCODE -ne 0) { throw "Failed to install runtime packages" }

Write-Host "[OK] Runtime dependencies installed."
